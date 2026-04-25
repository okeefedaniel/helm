"""Tests for the notify_due_tasks management command + its scheduling registration.

Pins:
- The command is registered with keel.scheduling under slug
  'helm-notify-due-tasks'.
- Due-soon and overdue notifications fire correctly.
- Idempotency via last_*_notif_at fields prevents duplicate dispatches.
- The scheduling decorator wraps handle() so a CommandRun row is written.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Notification
from keel.scheduling.models import CommandRun, ScheduledJob
from keel.scheduling.registry import job_registry
from tasks.models import Task
from tasks.services import claim_project, create_project, create_task

User = get_user_model()


@override_settings(HELM_TASKS_ENABLED=True)
class NotifyDueTasksCommandTests(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username='lead', email='lead@t.local')
        self.assignee = User.objects.create_user(username='asn', email='asn@t.local')
        self.project = create_project(name='X', user=self.lead)
        claim_project(project=self.project, user=self.lead)
        # Sync scheduled jobs so the wrapper has a row to write CommandRun against.
        call_command('sync_scheduled_jobs')

    def _make_task(self, *, due_offset_days, status=Task.Status.TODO, assignee=None):
        today = timezone.localdate()
        task = create_task(
            project=self.project, title=f'Task due {due_offset_days}d',
            user=self.lead, assignee=assignee or self.assignee,
            due_date=today + timedelta(days=due_offset_days),
        )
        if status != Task.Status.TODO:
            task.status = status
            task.save(update_fields=['status'])
        return task

    def test_due_soon_fires_for_tasks_due_today_or_tomorrow(self):
        task_today = self._make_task(due_offset_days=0)
        task_tomorrow = self._make_task(due_offset_days=1)
        Notification.objects.all().delete()

        call_command('notify_due_tasks')

        # Both tasks generate a Task Due Soon notification for the assignee.
        notifs = Notification.objects.filter(
            recipient=self.assignee, title__contains='Task Due Soon',
        )
        self.assertEqual(notifs.count(), 2)
        # Idempotency timestamps stamped.
        for t in (task_today, task_tomorrow):
            t.refresh_from_db()
            self.assertIsNotNone(t.last_due_soon_notif_at)

    def test_due_soon_idempotent_within_day(self):
        self._make_task(due_offset_days=0)
        call_command('notify_due_tasks')
        first_count = Notification.objects.filter(title__contains='Task Due Soon').count()
        # Re-run immediately — no duplicates.
        call_command('notify_due_tasks')
        second_count = Notification.objects.filter(title__contains='Task Due Soon').count()
        self.assertEqual(first_count, second_count)

    def test_overdue_fires_for_past_due_undone_tasks(self):
        task = self._make_task(due_offset_days=-3)
        Notification.objects.all().delete()
        call_command('notify_due_tasks')
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.assignee, title__contains='Task Overdue',
            ).count(),
            1,
        )
        task.refresh_from_db()
        self.assertIsNotNone(task.last_overdue_notif_at)

    def test_overdue_skips_done_tasks(self):
        self._make_task(due_offset_days=-3, status=Task.Status.DONE)
        Notification.objects.all().delete()
        call_command('notify_due_tasks')
        self.assertEqual(
            Notification.objects.filter(title__contains='Task Overdue').count(), 0,
        )

    def test_overdue_24h_cooldown(self):
        task = self._make_task(due_offset_days=-3)
        # Pretend we already notified an hour ago.
        task.last_overdue_notif_at = timezone.now() - timedelta(hours=1)
        task.save(update_fields=['last_overdue_notif_at'])
        Notification.objects.all().delete()
        call_command('notify_due_tasks')
        # No new notification — within cooldown.
        self.assertEqual(
            Notification.objects.filter(title__contains='Task Overdue').count(), 0,
        )

    def test_command_run_recorded_via_decorator(self):
        self._make_task(due_offset_days=0)
        runs_before = CommandRun.objects.filter(job__slug='helm-notify-due-tasks').count()
        call_command('notify_due_tasks')
        runs_after = CommandRun.objects.filter(job__slug='helm-notify-due-tasks').count()
        self.assertEqual(runs_after, runs_before + 1)
        run = CommandRun.objects.filter(job__slug='helm-notify-due-tasks').latest('started_at')
        self.assertEqual(run.status, CommandRun.Status.SUCCESS)
        self.assertIsNotNone(run.duration_ms)

    def test_dry_run_does_not_dispatch_or_stamp(self):
        task = self._make_task(due_offset_days=0)
        Notification.objects.all().delete()
        call_command('notify_due_tasks', '--dry-run')
        self.assertEqual(Notification.objects.count(), 0)
        task.refresh_from_db()
        self.assertIsNone(task.last_due_soon_notif_at)


@override_settings(HELM_TASKS_ENABLED=True)
class SchedulingRegistrationTests(TestCase):
    def test_notify_due_tasks_is_registered(self):
        spec = job_registry.get('helm-notify-due-tasks')
        self.assertIsNotNone(spec)
        self.assertEqual(spec.owner_product, 'helm')
        self.assertEqual(spec.cron_expression, '0 9 * * *')

    def test_sync_creates_db_row_for_helm_job(self):
        call_command('sync_scheduled_jobs')
        self.assertTrue(
            ScheduledJob.objects.filter(slug='helm-notify-due-tasks').exists(),
        )
