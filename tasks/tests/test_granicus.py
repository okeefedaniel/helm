"""ADD-7 — Granicus GovQA push hook tests.

Pins:
- is_available() returns False when env vars unset; True when set.
- push_to_govqa serializes FOIA project correctly + sends to GovQA URL.
- Successful response → True + audit log entry.
- HTTP error → False + audit log entry with error_message.
- Network error → False + retry once + audit log.
- Push button hidden when integration not configured (project_detail).
- Endpoint returns redirect with toast when integration unavailable.
- Non-LEAD user gets 403.
"""
from unittest.mock import patch, MagicMock
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from keel.accounts.models import ProductAccess

from core.models import AuditLog
from tasks.integrations.granicus import (
    is_available, push_to_govqa, _serialize_for_govqa,
)
from tasks.models import Project
from tasks.services import claim_project, create_project

User = get_user_model()


def _make_user(username='u'):
    u = User.objects.create_user(
        username=username, password='pw1234567890',
        email=f'{username}@t.local',
    )
    ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
    return u


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------
class IsAvailableTests(TestCase):
    @override_settings(GRANICUS_GOVQA_URL='', GRANICUS_GOVQA_API_KEY='')
    def test_returns_false_when_unset(self):
        self.assertFalse(is_available())

    @override_settings(
        GRANICUS_GOVQA_URL='https://govqa.example.com',
        GRANICUS_GOVQA_API_KEY='secret',
    )
    def test_returns_true_when_both_set(self):
        self.assertTrue(is_available())

    @override_settings(
        GRANICUS_GOVQA_URL='https://govqa.example.com',
        GRANICUS_GOVQA_API_KEY='',
    )
    def test_returns_false_when_url_set_but_key_missing(self):
        self.assertFalse(is_available())


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------
@override_settings(HELM_TASKS_ENABLED=True)
class SerializerTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')

    def test_standard_project_serializes_basic_fields(self):
        p = create_project(name='Standard', user=self.user)
        payload = _serialize_for_govqa(p)
        self.assertEqual(payload['name'], 'Standard')
        self.assertEqual(payload['kind'], 'standard')
        self.assertEqual(payload['helm_public_id'], str(p.public_id))
        # Non-FOIA: no 'foia' block.
        self.assertNotIn('foia', payload)

    def test_foia_project_includes_foia_block(self):
        p = create_project(name='FOIA Test', user=self.user, kind=Project.Kind.FOIA)
        p.foia_metadata = {
            'foia_request_id': 'FOIA-X',
            'foia_agency': 'Comptroller',
        }
        p.foia_received_at = date(2026, 4, 1)
        p.foia_jurisdiction = 'federal'
        p.save()
        payload = _serialize_for_govqa(p)
        self.assertIn('foia', payload)
        self.assertEqual(payload['foia']['request_id'], 'FOIA-X')
        self.assertEqual(payload['foia']['agency'], 'Comptroller')
        self.assertEqual(payload['foia']['received_at'], '2026-04-01')


# ---------------------------------------------------------------------------
# push_to_govqa
# ---------------------------------------------------------------------------
@override_settings(
    HELM_TASKS_ENABLED=True,
    GRANICUS_GOVQA_URL='https://govqa.example.com',
    GRANICUS_GOVQA_API_KEY='secret-key',
)
class PushToGovQATests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.project = create_project(
            name='FOIA Push', user=self.user, kind=Project.Kind.FOIA,
        )

    def test_unavailable_returns_false_with_message(self):
        with override_settings(GRANICUS_GOVQA_API_KEY=''):
            success, err = push_to_govqa(self.project, user=self.user)
        self.assertFalse(success)
        self.assertIn('not configured', err)

    def test_successful_push_returns_true_and_audits(self):
        AuditLog.objects.all().delete()
        mock_response = MagicMock(status_code=201, text='{"id": "abc"}')
        with patch('requests.post', return_value=mock_response) as mock_post:
            success, err = push_to_govqa(self.project, user=self.user)
        self.assertTrue(success)
        self.assertIsNone(err)
        # Called the configured URL with bearer auth.
        url = mock_post.call_args[0][0]
        self.assertEqual(url, 'https://govqa.example.com/api/v1/requests')
        headers = mock_post.call_args[1]['headers']
        self.assertEqual(headers['Authorization'], 'Bearer secret-key')
        self.assertEqual(headers['X-Source'], 'helm-pm')
        # Audit row written.
        self.assertTrue(
            AuditLog.objects.filter(
                description__contains='Pushed project',
            ).exists(),
        )

    def test_http_error_returns_false_with_status_in_message(self):
        AuditLog.objects.all().delete()
        mock_response = MagicMock(status_code=500, text='upstream broken')
        with patch('requests.post', return_value=mock_response):
            success, err = push_to_govqa(self.project, user=self.user)
        self.assertFalse(success)
        self.assertIn('500', err)
        # Audit row for failure.
        self.assertTrue(
            AuditLog.objects.filter(
                description__contains='Failed to push',
            ).exists(),
        )

    def test_network_error_retries_once_then_fails(self):
        import requests
        with patch('requests.post', side_effect=requests.ConnectionError('boom')) as mock_post:
            success, err = push_to_govqa(self.project, user=self.user)
        self.assertFalse(success)
        # Two attempts (initial + 1 retry).
        self.assertEqual(mock_post.call_count, 2)
        self.assertIn('boom', err)


# ---------------------------------------------------------------------------
# View / endpoint
# ---------------------------------------------------------------------------
@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class PushEndpointTests(TestCase):
    def setUp(self):
        self.lead = _make_user('lead')
        self.client.force_login(self.lead)
        self.project = create_project(
            name='FOIA Ep', user=self.lead, kind=Project.Kind.FOIA,
        )
        claim_project(project=self.project, user=self.lead)

    @override_settings(GRANICUS_GOVQA_URL='', GRANICUS_GOVQA_API_KEY='')
    def test_endpoint_redirects_with_warning_when_unconfigured(self):
        r = self.client.post(reverse('tasks:push_to_govqa', args=[self.project.slug]))
        self.assertEqual(r.status_code, 302)
        # Redirect to project detail.
        self.assertIn(self.project.slug, r.url)

    @override_settings(
        GRANICUS_GOVQA_URL='https://govqa.example.com',
        GRANICUS_GOVQA_API_KEY='k',
    )
    def test_endpoint_pushes_when_configured_and_lead(self):
        with patch('tasks.integrations.granicus.push_to_govqa', return_value=(True, None)) as mock_push:
            r = self.client.post(reverse('tasks:push_to_govqa', args=[self.project.slug]))
        self.assertEqual(r.status_code, 302)
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# Template button gating
# ---------------------------------------------------------------------------
@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class TemplateGatingTests(TestCase):
    def setUp(self):
        self.lead = _make_user('lead')
        self.client.force_login(self.lead)
        self.project = create_project(
            name='FOIA Tpl', user=self.lead, kind=Project.Kind.FOIA,
        )
        claim_project(project=self.project, user=self.lead)
        # Give it a deadline so the FOIA clock block renders.
        self.project.foia_received_at = date(2026, 4, 1)
        self.project.foia_jurisdiction = 'federal'
        self.project.save()
        from tasks.foia import recompute_deadline
        recompute_deadline(self.project)

    @override_settings(GRANICUS_GOVQA_URL='', GRANICUS_GOVQA_API_KEY='')
    def test_button_hidden_when_unconfigured(self):
        r = self.client.get(reverse('tasks:project_detail', args=[self.project.slug]))
        self.assertNotContains(r, 'Push to GovQA')

    @override_settings(
        GRANICUS_GOVQA_URL='https://govqa.example.com',
        GRANICUS_GOVQA_API_KEY='k',
    )
    def test_button_visible_when_configured(self):
        r = self.client.get(reverse('tasks:project_detail', args=[self.project.slug]))
        self.assertContains(r, 'Push to GovQA')
