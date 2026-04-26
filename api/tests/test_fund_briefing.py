"""ADD-6 — Fund-source aware briefing tests.

Pins:
- /api/v1/briefing/?include=fund_sources adds the rollup.
- Default (no include param) does NOT include fund_sources (kept light).
- Rollup respects per-user visibility: stranger sees none, creator sees all.
- Aggregation correctness: amounts sum, project lists are populated.
- Non-CIP projects with fund_sources don't pollute the rollup.
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


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class FundSourceBriefingTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)

        # Two CIP projects, both visible.
        self.arpa = create_project(
            name='ARPA Project', user=self.user, kind=Project.Kind.CIP,
        )
        self.arpa.fund_sources = [
            {'source': 'arpa', 'amount_cents': 240000000},
            {'source': 'state_match', 'amount_cents': 60000000},
        ]
        self.arpa.save()

        self.iija = create_project(
            name='IIJA Project', user=self.user, kind=Project.Kind.CIP,
        )
        self.iija.fund_sources = [
            {'source': 'iija', 'amount_cents': 1600000000},
            {'source': 'state_match', 'amount_cents': 400000000},
        ]
        self.iija.save()

        # Standard project with rogue fund_sources — should NOT pollute.
        self.std = create_project(name='Standard', user=self.user)
        self.std.fund_sources = [
            {'source': 'arpa', 'amount_cents': 999999},
        ]
        self.std.save()

    def test_default_briefing_omits_fund_sources(self):
        r = self.client.get(reverse('api:briefing'))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('fund_sources', r.json())

    def test_include_fund_sources_adds_rollup(self):
        r = self.client.get(reverse('api:briefing') + '?include=fund_sources')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn('fund_sources', data)
        sources = {row['source'] for row in data['fund_sources']}
        self.assertEqual(sources, {'arpa', 'iija', 'state_match'})

    def test_aggregation_sums_amounts(self):
        r = self.client.get(reverse('api:briefing') + '?include=fund_sources')
        rollup = {row['source']: row for row in r.json()['fund_sources']}
        # state_match appears in both ARPA and IIJA projects → total $4.6M
        self.assertEqual(
            rollup['state_match']['committed_cents'], 60000000 + 400000000,
        )
        self.assertEqual(rollup['state_match']['project_count'], 2)

    def test_non_cip_projects_excluded_from_rollup(self):
        r = self.client.get(reverse('api:briefing') + '?include=fund_sources')
        rollup = {row['source']: row for row in r.json()['fund_sources']}
        # The Standard project's $999999 ARPA entry should NOT count.
        # Only the CIP ARPA project ($240M) shows.
        self.assertEqual(rollup['arpa']['committed_cents'], 240000000)
        self.assertEqual(rollup['arpa']['project_count'], 1)

    def test_stranger_sees_no_fund_sources(self):
        stranger = _make_user('stranger')
        self.client.logout()
        self.client.force_login(stranger)
        r = self.client.get(reverse('api:briefing') + '?include=fund_sources')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['fund_sources'], [])

    def test_rollup_includes_project_list(self):
        r = self.client.get(reverse('api:briefing') + '?include=fund_sources')
        rollup = {row['source']: row for row in r.json()['fund_sources']}
        arpa_projects = rollup['arpa']['projects']
        self.assertEqual(len(arpa_projects), 1)
        self.assertEqual(arpa_projects[0]['slug'], self.arpa.slug)

    def test_rollup_marks_harbor_unavailable_when_snapshot_missing(self):
        # No Harbor CachedFeedSnapshot in the test DB → harbor_unavailable=True.
        r = self.client.get(reverse('api:briefing') + '?include=fund_sources')
        rollup = {row['source']: row for row in r.json()['fund_sources']}
        self.assertTrue(rollup['arpa']['harbor_unavailable'])
        # Helm-only fields still populated correctly.
        self.assertEqual(rollup['arpa']['committed_cents'], 240000000)
        self.assertEqual(rollup['arpa']['drawn_cents'], 0)
        self.assertEqual(rollup['arpa']['remaining_cents'], 240000000)

    def test_rollup_joins_harbor_drawdown_when_snapshot_present(self):
        # Seed Harbor's snapshot with fund_source_breakdown for ARPA.
        from django.utils import timezone
        from dashboard.models import CachedFeedSnapshot
        CachedFeedSnapshot.objects.create(
            product='harbor',
            feed_data={
                'fund_source_breakdown': {
                    'arpa': {
                        'award_count': 1,
                        'award_value_cents': 200000000,  # $2M obligated
                        'drawn_cents': 80000000,         # $800k drawn
                        'paid_cents': 75000000,
                        'refunded_cents': 0,
                    },
                },
            },
            fetched_at=timezone.now(),
            is_stale=False,
            consecutive_failures=0,
        )
        r = self.client.get(reverse('api:briefing') + '?include=fund_sources')
        rollup = {row['source']: row for row in r.json()['fund_sources']}
        self.assertFalse(rollup['arpa']['harbor_unavailable'])
        self.assertEqual(rollup['arpa']['obligated_cents'], 200000000)
        self.assertEqual(rollup['arpa']['drawn_cents'], 80000000)
        # remaining = Helm-committed ($2.4M) minus Harbor-drawn ($800k) = $1.6M
        self.assertEqual(rollup['arpa']['remaining_cents'], 240000000 - 80000000)
