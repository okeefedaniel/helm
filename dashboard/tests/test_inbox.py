"""Tests for dashboard.inbox.InboxAggregator."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from dashboard.inbox import InboxAggregator, get_user_oidc_sub
from dashboard.models import CachedFeedSnapshot

User = get_user_model()

FAKE_FLEET = [
    {
        'key': 'manifest', 'label': 'Manifest', 'icon': 'bi-pen',
        'url': 'https://manifest.test/dashboard/',
        'feed_url': 'https://manifest.test/api/v1/helm-feed/',
        'tagline': 'Signing',
    },
    {
        'key': 'harbor', 'label': 'Harbor', 'icon': 'bi-bank2',
        'url': 'https://harbor.test/dashboard/',
        'feed_url': 'https://harbor.test/api/v1/helm-feed/',
        'tagline': 'Grants',
    },
]


def _fake_response(status_code=200, json_data=None):
    resp = mock.MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


def _request_with_sub(sub):
    rf = RequestFactory()
    req = rf.get('/dashboard/')
    req.session = {'keel_oidc_claims': {'sub': sub}}
    return req


@override_settings(FLEET_PRODUCTS=FAKE_FLEET, HELM_FEED_API_KEY='test-key')
class InboxAggregatorTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='u1', email='u1@t.local')
        self.req = _request_with_sub('sub-u1')

    def test_happy_path_aggregates_items(self):
        manifest_payload = {
            'product': 'manifest', 'product_label': 'Manifest',
            'product_url': 'https://manifest.test',
            'user_sub': 'sub-u1',
            'items': [
                {'id': 's1', 'type': 'signature', 'title': 'Sign A',
                 'deep_link': 'https://manifest.test/p/1/',
                 'waiting_since': '2026-04-26T10:00:00Z',
                 'priority': 'high'},
            ],
            'unread_notifications': [],
            'fetched_at': '2026-04-26T12:00:00Z',
        }
        # Harbor not yet implemented — 404 → fallback
        with mock.patch('dashboard.inbox.requests.get') as req_get:
            req_get.side_effect = lambda url, **kw: (
                _fake_response(200, manifest_payload) if 'manifest' in url
                else _fake_response(404)
            )
            agg = InboxAggregator(self.user, self.req)
            payloads = agg.get_per_product()

        by_key = {p['product']: p for p in payloads}
        self.assertEqual(by_key['manifest']['item_count'], 1)
        self.assertFalse(by_key['manifest']['unfiltered'])
        # Harbor fallback (404 → unfiltered aggregate count)
        self.assertTrue(by_key['harbor']['unfiltered'])
        self.assertEqual(by_key['harbor']['item_count'], 0)

    def test_unreachable_peer_marked(self):
        with mock.patch('dashboard.inbox.requests.get') as req_get:
            import requests as _req
            req_get.side_effect = _req.ConnectionError('boom')
            agg = InboxAggregator(self.user, self.req)
            payloads = agg.get_per_product()
        for p in payloads:
            self.assertTrue(p['unreachable'])

    def test_timeout_retries_once_then_succeeds(self):
        # Cold-start scenario: first request times out, second lands on a
        # warm container and succeeds.
        import requests as _req
        manifest_payload = {
            'product': 'manifest', 'product_label': 'Manifest',
            'product_url': '', 'user_sub': 'sub-u1',
            'items': [], 'unread_notifications': [], 'fetched_at': '',
        }
        manifest_calls = []

        def fake_get(url, **kw):
            if 'manifest' in url:
                manifest_calls.append(url)
                if len(manifest_calls) == 1:
                    raise _req.ReadTimeout('cold start')
                return _fake_response(200, manifest_payload)
            return _fake_response(404)

        with mock.patch('dashboard.inbox.requests.get', side_effect=fake_get):
            agg = InboxAggregator(self.user, self.req)
            payloads = {p['product']: p for p in agg.get_per_product()}

        self.assertEqual(len(manifest_calls), 2)
        self.assertFalse(payloads['manifest'].get('unreachable'))
        self.assertFalse(payloads['manifest']['unfiltered'])

    def test_timeout_retries_once_then_gives_up(self):
        # Both attempts time out → fall back as unreachable, no third try.
        import requests as _req
        calls = {'manifest': 0}

        def fake_get(url, **kw):
            if 'manifest' in url:
                calls['manifest'] += 1
                raise _req.ReadTimeout('still cold')
            return _fake_response(404)

        with mock.patch('dashboard.inbox.requests.get', side_effect=fake_get):
            agg = InboxAggregator(self.user, self.req)
            payloads = {p['product']: p for p in agg.get_per_product()}

        self.assertEqual(calls['manifest'], 2)
        self.assertTrue(payloads['manifest']['unreachable'])

    def test_per_user_cache_isolation(self):
        manifest_u1 = {'product': 'manifest', 'product_label': 'Manifest',
                       'product_url': '', 'user_sub': 'sub-u1',
                       'items': [{'id': '1', 'type': 'signature', 'title': 'U1 only',
                                  'deep_link': '', 'waiting_since': '',
                                  'priority': 'normal'}],
                       'unread_notifications': [], 'fetched_at': ''}
        manifest_u2 = {'product': 'manifest', 'product_label': 'Manifest',
                       'product_url': '', 'user_sub': 'sub-u2',
                       'items': [],
                       'unread_notifications': [], 'fetched_at': ''}

        def fake_get(url, **kw):
            sub = kw.get('params', {}).get('user_sub', '')
            if 'manifest' not in url:
                return _fake_response(404)
            return _fake_response(200, manifest_u1 if sub == 'sub-u1' else manifest_u2)

        u2 = User.objects.create_user(username='u2', email='u2@t.local')
        req2 = _request_with_sub('sub-u2')

        with mock.patch('dashboard.inbox.requests.get', side_effect=fake_get):
            agg1 = InboxAggregator(self.user, self.req)
            agg2 = InboxAggregator(u2, req2)
            p1 = {p['product']: p for p in agg1.get_per_product()}
            p2 = {p['product']: p for p in agg2.get_per_product()}

        self.assertEqual(p1['manifest']['item_count'], 1)
        self.assertEqual(p2['manifest']['item_count'], 0)

    def test_no_user_sub_falls_back(self):
        # Anonymous-ish user with no sub: aggregator must not crash and
        # must return aggregate-count fallback for every peer.
        rf = RequestFactory()
        req = rf.get('/dashboard/')
        req.session = {}
        CachedFeedSnapshot.objects.create(
            product='manifest',
            feed_data={
                'product': 'manifest', 'product_label': 'Manifest',
                'product_url': '', 'metrics': [],
                'action_items': [{'id': 'a', 'type': 'signature', 'title': 't'}],
                'alerts': [], 'sparklines': {},
            },
            fetched_at='2026-04-26T12:00:00Z',
        )
        agg = InboxAggregator(self.user, req)
        payloads = {p['product']: p for p in agg.get_per_product()}
        self.assertTrue(payloads['manifest']['unfiltered'])
        self.assertEqual(payloads['manifest']['aggregate_count'], 1)

    @override_settings(FLEET_PRODUCTS=[])
    def test_empty_fleet_returns_empty(self):
        agg = InboxAggregator(self.user, self.req)
        self.assertEqual(agg.get_per_product(), [])

    def test_aggregated_notifications_sorted_newest_first(self):
        payload = {
            'product': 'manifest', 'product_label': 'Manifest',
            'product_url': '', 'user_sub': 'sub-u1', 'items': [],
            'unread_notifications': [
                {'id': 'n1', 'title': 'old', 'body': '', 'deep_link': '',
                 'created_at': '2026-04-20T10:00:00Z', 'priority': 'normal'},
                {'id': 'n2', 'title': 'new', 'body': '', 'deep_link': '',
                 'created_at': '2026-04-26T11:00:00Z', 'priority': 'normal'},
            ],
            'fetched_at': '',
        }
        with mock.patch('dashboard.inbox.requests.get') as req_get:
            req_get.side_effect = lambda url, **kw: (
                _fake_response(200, payload) if 'manifest' in url
                else _fake_response(404)
            )
            agg = InboxAggregator(self.user, self.req)
            notes = agg.get_aggregated_unread_notifications()
        titles = [n['title'] for n in notes]
        self.assertEqual(titles, ['new', 'old'])


class GetUserOidcSubTests(TestCase):

    def test_session_claim_preferred(self):
        rf = RequestFactory()
        req = rf.get('/')
        req.session = {'keel_oidc_claims': {'sub': 'session-sub'}}
        u = User.objects.create_user(username='x', email='x@t.local')
        self.assertEqual(get_user_oidc_sub(u, req), 'session-sub')

    def test_falls_back_to_socialaccount(self):
        from allauth.socialaccount.models import SocialAccount
        u = User.objects.create_user(username='y', email='y@t.local')
        SocialAccount.objects.create(user=u, provider='keel', uid='sa-sub')
        rf = RequestFactory()
        req = rf.get('/')
        req.session = {}
        self.assertEqual(get_user_oidc_sub(u, req), 'sa-sub')

    def test_unauthenticated_returns_empty(self):
        from django.contrib.auth.models import AnonymousUser
        rf = RequestFactory()
        req = rf.get('/')
        req.session = {}
        self.assertEqual(get_user_oidc_sub(AnonymousUser(), req), '')
