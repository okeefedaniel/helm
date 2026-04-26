"""Smoke tests for the restructured dashboard (tab nav + 3-column today)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

User = get_user_model()


@override_settings(
    HELM_TASKS_ENABLED=True,
    FLEET_PRODUCTS=[
        {'key': 'manifest', 'label': 'Manifest', 'icon': 'bi-pen',
         'url': 'https://manifest.test/dashboard/',
         'feed_url': 'https://manifest.test/api/v1/helm-feed/',
         'tagline': 'Signing'},
    ],
    HELM_FEED_API_KEY='',  # forces fallback path; no live HTTP
)
class DashboardRenderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='dok', email='dok@t.local', password='x',
            is_superuser=True, is_staff=True,  # bypass ProductAccessMiddleware
        )
        self.client.force_login(self.user)

    def test_today_tab_renders_by_default(self):
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # Tab nav present
        self.assertIn('id="dashboard-tabs"', body)
        # Today tab is the active default
        self.assertIn('id="tab-today"', body)
        self.assertIn('id="tab-suite"', body)
        # Three columns present
        self.assertIn('My Work', body)
        self.assertIn('Awaiting Me', body)
        self.assertIn('Alerts', body)

    def test_suite_tab_active_via_query(self):
        resp = self.client.get('/dashboard/?tab=suite')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # Suite-tab pane should be the show/active one
        self.assertIn('Across the suite', body)

    def test_drilldown_renders_for_known_product(self):
        resp = self.client.get(
            reverse('dashboard:product-drilldown', kwargs={'product': 'manifest'})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Manifest', resp.content.decode())

    def test_drilldown_404s_for_unknown_product(self):
        resp = self.client.get(
            reverse('dashboard:product-drilldown', kwargs={'product': 'nope'})
        )
        self.assertEqual(resp.status_code, 404)

    def test_partials_return_200(self):
        for name in (
            'dashboard:partial-deadline-rail',
            'dashboard:partial-inbox-column',
            'dashboard:partial-alerts-column',
        ):
            with self.subTest(partial=name):
                resp = self.client.get(reverse(name))
                self.assertEqual(resp.status_code, 200)
