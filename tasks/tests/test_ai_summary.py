"""ADD-4 — AI project summary tests.

Pins:
- summarize_project calls Claude with project context, returns text.
- Output is cached on (public_id, updated_at); cache-hit returns same.
- Project mutation invalidates cache (different updated_at = different key).
- can_summarize correctly excludes OBSERVERs and unauthenticated users.
- /summarize/ endpoint enforces can_summarize → 403 for OBSERVER.
- /summarize/ writes an AuditLog row.
- No-API-key state returns a graceful fallback message, not a 500.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from keel.accounts.models import ProductAccess

from core.models import AuditLog
from tasks.access import can_summarize
from tasks.ai import _build_user_message, _cache_key, summarize_project
from tasks.models import ProjectCollaborator
from tasks.services import (
    add_project_collaborator, add_project_note, claim_project,
    create_project, create_task,
)

User = get_user_model()


def _make_user(username='u', staff=False):
    u = User.objects.create_user(
        username=username, password='pw1234567890',
        email=f'{username}@t.local', is_staff=staff,
    )
    ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
    return u


@override_settings(HELM_TASKS_ENABLED=True)
class SummarizeProjectTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user('u')
        self.project = create_project(
            name='Q3 Audit', user=self.user,
            description='Quarterly audit project for AI summary test.',
        )
        claim_project(project=self.project, user=self.user)
        create_task(project=self.project, title='Open task', user=self.user)
        add_project_note(project=self.project, user=self.user, content='Note A')

    def test_no_api_key_returns_graceful_message(self):
        # get_client() returns None when no key; summarize_project should
        # surface a friendly message, never raise.
        with patch('tasks.ai.get_client', return_value=None):
            result = summarize_project(self.project)
        self.assertIn('AI summary unavailable', result)

    def test_summarize_calls_claude_with_project_context(self):
        with patch('tasks.ai.get_client', return_value=object()) as mock_client, \
             patch('tasks.ai.call_claude', return_value='AI: status looks good.') as mock_call:
            result = summarize_project(self.project)
        self.assertEqual(result, 'AI: status looks good.')
        # Claude received the structured project context in user_message.
        _args, kwargs = mock_call.call_args
        user_msg = kwargs.get('user_message') or _args[2]
        self.assertIn('Q3 Audit', user_msg)
        self.assertIn('Open task', user_msg)
        self.assertIn('Note A', user_msg[:5000])  # within first 5KB

    def test_cache_hit_does_not_recall_claude(self):
        with patch('tasks.ai.get_client', return_value=object()), \
             patch('tasks.ai.call_claude', return_value='cached output') as mock_call:
            summarize_project(self.project)
            summarize_project(self.project)
        # Two summarize calls, but Claude only called once (second was cached).
        self.assertEqual(mock_call.call_count, 1)

    def test_force_refresh_skips_cache(self):
        with patch('tasks.ai.get_client', return_value=object()), \
             patch('tasks.ai.call_claude', side_effect=['v1', 'v2']) as mock_call:
            r1 = summarize_project(self.project)
            r2 = summarize_project(self.project, force_refresh=True)
        self.assertEqual(r1, 'v1')
        self.assertEqual(r2, 'v2')
        self.assertEqual(mock_call.call_count, 2)

    def test_project_mutation_invalidates_cache_via_updated_at(self):
        # First summarize — cache key1 includes updated_at_1.
        with patch('tasks.ai.get_client', return_value=object()), \
             patch('tasks.ai.call_claude', return_value='v1'):
            summarize_project(self.project)
            key1 = _cache_key(self.project)
        # Mutate the project — updated_at changes, cache key changes.
        self.project.description = 'Updated description'
        self.project.save()
        self.project.refresh_from_db()
        key2 = _cache_key(self.project)
        self.assertNotEqual(key1, key2)

    def test_claude_returns_none_surfaces_failure_message(self):
        with patch('tasks.ai.get_client', return_value=object()), \
             patch('tasks.ai.call_claude', return_value=None):
            result = summarize_project(self.project)
        self.assertIn('AI summary failed', result)

    def test_user_message_includes_foia_clock_when_applicable(self):
        from datetime import date, timedelta
        from tasks.foia import recompute_deadline
        foia = create_project(name='FOIA Test', user=self.user, kind='foia')
        foia.foia_received_at = date.today() - timedelta(days=5)
        foia.foia_jurisdiction = 'federal'
        foia.save(update_fields=['foia_received_at', 'foia_jurisdiction'])
        recompute_deadline(foia)

        msg = _build_user_message(foia)
        self.assertIn('FOIA STATUTORY DEADLINE', msg)
        self.assertIn('business days remaining', msg)


@override_settings(HELM_TASKS_ENABLED=True)
class CanSummarizeAccessTests(TestCase):
    def setUp(self):
        self.lead = _make_user('lead')
        self.contributor = _make_user('contrib')
        self.observer = _make_user('obs')
        self.stranger = _make_user('stranger')
        self.admin = _make_user('admin', staff=True)
        self.project = create_project(name='ACL Test', user=self.lead)

        add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.contributor,
            role=ProjectCollaborator.Role.CONTRIBUTOR,
        )
        add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.observer,
            role=ProjectCollaborator.Role.OBSERVER,
        )

    def test_creator_can_summarize(self):
        self.assertTrue(can_summarize(self.lead, self.project))

    def test_contributor_can_summarize(self):
        self.assertTrue(can_summarize(self.contributor, self.project))

    def test_observer_cannot_summarize(self):
        # OBSERVER is read-only; AI is a write-shaped action (cost, audit).
        self.assertFalse(can_summarize(self.observer, self.project))

    def test_stranger_cannot_summarize(self):
        self.assertFalse(can_summarize(self.stranger, self.project))

    def test_staff_admin_bypasses_role_check(self):
        self.assertTrue(can_summarize(self.admin, self.project))

    def test_anonymous_cannot_summarize(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(can_summarize(AnonymousUser(), self.project))


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class SummarizeEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.lead = _make_user('lead')
        self.observer = _make_user('obs')
        self.client.force_login(self.lead)
        self.project = create_project(name='Endpoint Test', user=self.lead)
        add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.observer,
            role=ProjectCollaborator.Role.OBSERVER,
        )

    def test_get_returns_summary_text(self):
        with patch('tasks.ai.get_client', return_value=object()), \
             patch('tasks.ai.call_claude', return_value='Project summary text.'):
            r = self.client.get(reverse('tasks:summarize_project', args=[self.project.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.decode('utf-8'), 'Project summary text.')
        self.assertIn('text/plain', r['Content-Type'])

    def test_post_forces_refresh(self):
        with patch('tasks.ai.get_client', return_value=object()), \
             patch('tasks.ai.call_claude', side_effect=['cached v1', 'fresh v2']) as mock_call:
            self.client.get(reverse('tasks:summarize_project', args=[self.project.slug]))
            r = self.client.post(reverse('tasks:summarize_project', args=[self.project.slug]))
        self.assertEqual(r.content.decode('utf-8'), 'fresh v2')
        self.assertEqual(mock_call.call_count, 2)

    def test_observer_gets_403(self):
        self.client.logout()
        self.client.force_login(self.observer)
        r = self.client.get(reverse('tasks:summarize_project', args=[self.project.slug]))
        self.assertEqual(r.status_code, 403)

    def test_audit_log_written_per_request(self):
        AuditLog.objects.all().delete()
        with patch('tasks.ai.get_client', return_value=object()), \
             patch('tasks.ai.call_claude', return_value='ok'):
            self.client.get(reverse('tasks:summarize_project', args=[self.project.slug]))
        self.assertTrue(
            AuditLog.objects.filter(
                action='export',
                description__contains='AI summary generated',
            ).exists(),
        )
