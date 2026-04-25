"""ADD-1 — CIP project type tests.

Pins:
- Project.Kind.CIP enum value present.
- fund_sources JSONField stores structured list and round-trips.
- Federal compliance flags default False, settable independently.
- Project list ?fund_source=arpa filter returns only matching CIP projects.
- Demo seed creates the 2 expected CIP projects with proper fund stacks.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from keel.accounts.models import ProductAccess

from tasks.models import Project
from tasks.services import create_project

User = get_user_model()


def _make_user(username='u'):
    u = User.objects.create_user(
        username=username, password='pw1234567890',
        email=f'{username}@t.local',
    )
    ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
    return u


@override_settings(HELM_TASKS_ENABLED=True)
class CIPModelTests(TestCase):
    def test_cip_kind_choice_present(self):
        choices = dict(Project.Kind.choices)
        self.assertIn('cip', choices)
        self.assertEqual(choices['cip'], 'Capital Improvement Plan')

    def test_fund_sources_default_empty_list(self):
        u = _make_user()
        p = create_project(name='Empty', user=u)
        self.assertEqual(p.fund_sources, [])

    def test_fund_sources_stores_structured_list(self):
        u = _make_user()
        p = create_project(name='ARPA', user=u, kind=Project.Kind.CIP)
        p.fund_sources = [
            {'source': 'arpa', 'amount_cents': 240000000, 'label': 'CPF'},
            {'source': 'state_match', 'amount_cents': 60000000},
        ]
        p.save()
        p.refresh_from_db()
        self.assertEqual(len(p.fund_sources), 2)
        self.assertEqual(p.fund_sources[0]['source'], 'arpa')
        self.assertEqual(p.fund_sources[0]['amount_cents'], 240000000)

    def test_compliance_flags_default_false(self):
        u = _make_user()
        p = create_project(name='Test', user=u, kind=Project.Kind.CIP)
        self.assertFalse(p.requires_davis_bacon)
        self.assertFalse(p.requires_baba)
        self.assertFalse(p.requires_nepa)
        self.assertFalse(p.requires_environmental_review)

    def test_compliance_flags_settable_independently(self):
        u = _make_user()
        p = create_project(name='Test', user=u, kind=Project.Kind.CIP)
        p.requires_davis_bacon = True
        p.requires_baba = True
        p.save()
        p.refresh_from_db()
        self.assertTrue(p.requires_davis_bacon)
        self.assertTrue(p.requires_baba)
        self.assertFalse(p.requires_nepa)


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class FundSourceFilterTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)
        self.arpa = create_project(
            name='ARPA Project', user=self.user, kind=Project.Kind.CIP,
        )
        self.arpa.fund_sources = [
            {'source': 'arpa', 'amount_cents': 100000000},
        ]
        self.arpa.save()

        self.iija = create_project(
            name='IIJA Project', user=self.user, kind=Project.Kind.CIP,
        )
        self.iija.fund_sources = [
            {'source': 'iija', 'amount_cents': 200000000},
        ]
        self.iija.save()

        # Standard project, no fund sources.
        self.std = create_project(name='Standard Project', user=self.user)

    def test_filter_arpa_returns_only_arpa(self):
        r = self.client.get(reverse('tasks:project_list') + '?fund_source=arpa')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ARPA Project')
        self.assertNotContains(r, 'IIJA Project')
        self.assertNotContains(r, 'Standard Project')

    def test_filter_unknown_returns_empty(self):
        r = self.client.get(reverse('tasks:project_list') + '?fund_source=nonsense')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'ARPA Project')
        self.assertNotContains(r, 'IIJA Project')

    def test_filter_kind_cip_returns_only_cip(self):
        r = self.client.get(reverse('tasks:project_list') + '?kind=cip')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ARPA Project')
        self.assertContains(r, 'IIJA Project')
        self.assertNotContains(r, 'Standard Project')

    def test_no_filter_returns_all(self):
        r = self.client.get(reverse('tasks:project_list'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ARPA Project')
        self.assertContains(r, 'IIJA Project')
        self.assertContains(r, 'Standard Project')


@override_settings(HELM_TASKS_ENABLED=True, DEMO_MODE=True)
class CIPDemoSeedTests(TestCase):
    def setUp(self):
        User.objects.create_user(
            username='dokadmin', email='dok@dok.net',
            is_superuser=True, is_staff=True,
        )

    def test_seed_creates_two_cip_projects(self):
        from django.core.management import call_command
        call_command('seed_demo_projects')
        cip_projects = Project.objects.filter(kind=Project.Kind.CIP)
        self.assertEqual(cip_projects.count(), 2)
        slugs = set(cip_projects.values_list('slug', flat=True))
        self.assertEqual(slugs, {
            'arpa-broadband-rollout',
            'iija-bridge-replacement',
        })

    def test_seed_arpa_project_has_correct_fund_stack(self):
        from django.core.management import call_command
        call_command('seed_demo_projects')
        arpa = Project.objects.get(slug='arpa-broadband-rollout')
        sources = {fs['source'] for fs in arpa.fund_sources}
        self.assertEqual(sources, {'arpa', 'state_match'})
        self.assertTrue(arpa.requires_davis_bacon)
        self.assertTrue(arpa.requires_baba)
        self.assertTrue(arpa.requires_environmental_review)

    def test_seed_iija_project_has_nepa_flag(self):
        from django.core.management import call_command
        call_command('seed_demo_projects')
        iija = Project.objects.get(slug='iija-bridge-replacement')
        self.assertTrue(iija.requires_nepa)
        sources = {fs['source'] for fs in iija.fund_sources}
        self.assertIn('iija', sources)
