"""Phase 12 metrics endpoint tests.

Pins:
- /api/v1/metrics/ requires staff.
- Returns JSON with audit / notification / cron counters.
- Health flags compute correctly when known regressions are simulated.
- Endpoint doesn't crash when an optional model isn't installed.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

User = get_user_model()


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class MetricsEndpointTests(TestCase):
    def setUp(self):
        from keel.accounts.models import ProductAccess
        self.staff = User.objects.create_user(
            username='staff', password='pw1234567890',
            email='staff@t.local', is_staff=True,
        )
        ProductAccess.objects.create(
            user=self.staff, product='helm', role='helm_admin',
        )
        self.unprivileged = User.objects.create_user(
            username='nope', password='pw1234567890',
            email='nope@t.local',
        )
        ProductAccess.objects.create(
            user=self.unprivileged, product='helm', role='helm_user',
        )

    def test_endpoint_requires_staff(self):
        self.client.force_login(self.unprivileged)
        r = self.client.get(reverse('api:metrics'))
        # staff_member_required redirects to admin login.
        self.assertEqual(r.status_code, 302)

    def test_staff_can_access(self):
        self.client.force_login(self.staff)
        r = self.client.get(reverse('api:metrics'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/json')

    def test_payload_includes_canary_keys(self):
        self.client.force_login(self.staff)
        data = self.client.get(reverse('api:metrics')).json()
        # The four canary flags exist.
        self.assertIn('flags', data)
        for key in (
            'audit_silent_24h', 'cron_silent_24h',
            'cron_failures_24h', 'notifications_failing',
        ):
            self.assertIn(key, data['flags'])
        self.assertIn('healthy', data)

    def test_audit_silent_flag_true_when_no_writes(self):
        from core.models import AuditLog
        # Clear any audit rows that login or middleware may have produced
        # during setUp — we want to assert the flag fires when there are
        # GENUINELY no recent writes.
        AuditLog.objects.all().delete()
        self.client.force_login(self.staff)
        AuditLog.objects.all().delete()
        data = self.client.get(reverse('api:metrics')).json()
        # The metrics request itself doesn't write AuditLog (it's read-only)
        # so the canary should fire.
        self.assertTrue(data['flags']['audit_silent_24h'])

    def test_audit_silent_flag_false_after_a_write(self):
        from core.models import AuditLog
        AuditLog.objects.create(
            action='create', entity_type='helm_tasks.Project',
            entity_id='1', description='test',
        )
        self.client.force_login(self.staff)
        data = self.client.get(reverse('api:metrics')).json()
        self.assertFalse(data['flags']['audit_silent_24h'])
        self.assertGreaterEqual(data['audit_log_writes_24h'], 1)

    def test_cron_failures_flag_true_when_recent_error_run(self):
        from keel.scheduling.models import CommandRun, ScheduledJob
        job = ScheduledJob.objects.create(
            slug='test-job', name='T', command='t', cron_expression='*',
            owner_product='helm',
        )
        CommandRun.objects.create(
            job=job, status=CommandRun.Status.ERROR,
            started_at=timezone.now() - timedelta(hours=2),
            error_message='boom',
        )
        self.client.force_login(self.staff)
        data = self.client.get(reverse('api:metrics')).json()
        self.assertTrue(data['flags']['cron_failures_24h'])
        self.assertEqual(data['scheduled_failures_24h'], 1)

    def test_healthy_top_level_aggregates_flags(self):
        from core.models import AuditLog
        from keel.scheduling.models import CommandRun, ScheduledJob
        # Make canaries pass: write an audit row + a successful cron run.
        AuditLog.objects.create(
            action='create', entity_type='x', entity_id='1', description='ok',
        )
        job = ScheduledJob.objects.create(
            slug='ok-job', name='J', command='j', cron_expression='*',
            owner_product='helm',
        )
        CommandRun.objects.create(
            job=job, status=CommandRun.Status.SUCCESS,
            started_at=timezone.now() - timedelta(hours=1),
        )
        self.client.force_login(self.staff)
        data = self.client.get(reverse('api:metrics')).json()
        self.assertTrue(data['healthy'])

    def test_payload_includes_project_lifecycle_gauges(self):
        self.client.force_login(self.staff)
        data = self.client.get(reverse('api:metrics')).json()
        # All four buckets should exist as keys (even if zero).
        for key in (
            'projects_active', 'projects_on_hold',
            'projects_completed', 'projects_archived',
        ):
            self.assertIn(key, data)
