"""Tests for the ``fetch_feeds`` management command.

Covers:
- Parallel execution is the default (all fetches overlap, not serialize).
- Per-fetch timeout + overall wall-clock budget both apply.
- Circuit breaker opens after N consecutive failures and skips the
  product until cooldown elapses.
- A successful fetch resets the circuit breaker.

Uses ``TransactionTestCase`` because the command spawns worker threads
and each thread uses its own DB connection — a plain ``TestCase``'s
transaction wouldn't be visible to them.
"""
import datetime
import threading
import time
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from dashboard.models import CachedFeedSnapshot


FAKE_FLEET = [
    {
        'code': 'harbor',
        'label': 'Harbor',
        'url': 'https://harbor.test/',
        'feed_url': 'https://harbor.test/feed/',
    },
    {
        'code': 'bounty',
        'label': 'Bounty',
        'url': 'https://bounty.test/',
        'feed_url': 'https://bounty.test/feed/',
    },
    {
        'code': 'beacon',
        'label': 'Beacon',
        'url': 'https://beacon.test/',
        'feed_url': 'https://beacon.test/feed/',
    },
]


def _ok(data=None):
    return {'ok': True, 'data': data or {'metrics': []}, 'error': '', 'duration_ms': 5}


def _fail(error='boom'):
    return {'ok': False, 'data': None, 'error': error, 'duration_ms': 10}


@override_settings(KEEL_FLEET_PRODUCTS=FAKE_FLEET, HELM_FEED_API_KEY='k', DEMO_MODE=False)
class FetchFeedsParallelTests(TransactionTestCase):
    def test_parallel_is_default(self):
        """All three fetches overlap — peak concurrency equals 3."""
        in_flight = []
        max_concurrent = [0]
        lock = threading.Lock()
        gate = threading.Event()

        def slow_fetch(url, api_key, timeout=None):
            with lock:
                in_flight.append(url)
                max_concurrent[0] = max(max_concurrent[0], len(in_flight))
            # Wait until all three are in flight, then release together so
            # the concurrency assertion is deterministic.
            if max_concurrent[0] >= 3:
                gate.set()
            gate.wait(timeout=2)
            with lock:
                in_flight.remove(url)
            return _ok()

        with mock.patch(
            'keel.feed.client.fetch_product_feed', side_effect=slow_fetch
        ) as m:
            call_command('fetch_feeds', stdout=StringIO())

        self.assertEqual(max_concurrent[0], 3)
        self.assertEqual(m.call_count, 3)

    def test_sequential_flag_disables_parallelism(self):
        in_flight = []
        peak = [0]
        lock = threading.Lock()

        def fetch(url, api_key, timeout=None):
            with lock:
                in_flight.append(url)
                peak[0] = max(peak[0], len(in_flight))
            with lock:
                in_flight.remove(url)
            return _ok()

        with mock.patch('keel.feed.client.fetch_product_feed', side_effect=fetch):
            call_command('fetch_feeds', '--sequential', stdout=StringIO())

        self.assertEqual(peak[0], 1)

    def test_overall_timeout_abandons_stragglers(self):
        """A product hanging past the budget does not stall the command."""
        release = threading.Event()

        def fetch(url, api_key, timeout=None):
            if 'harbor' in url:
                release.wait(timeout=5)
            return _ok()

        try:
            with mock.patch(
                'keel.feed.client.fetch_product_feed', side_effect=fetch
            ):
                out = StringIO()
                start = time.monotonic()
                call_command(
                    'fetch_feeds', '--overall-timeout=1', stdout=out
                )
                elapsed = time.monotonic() - start

            self.assertLess(elapsed, 2.0)
            self.assertIn('abandoned', out.getvalue())
        finally:
            release.set()  # let the straggler finish so it doesn't leak
            # Give the background thread a moment to write before teardown.
            time.sleep(0.2)

    def test_slow_product_does_not_stall_fast_ones(self):
        release = threading.Event()

        def fetch(url, api_key, timeout=None):
            if 'harbor' in url:
                release.wait(timeout=5)
            return _ok()

        try:
            with mock.patch(
                'keel.feed.client.fetch_product_feed', side_effect=fetch
            ):
                call_command(
                    'fetch_feeds', '--overall-timeout=1', stdout=StringIO()
                )

            fresh = set(
                CachedFeedSnapshot.objects.filter(is_stale=False).values_list(
                    'product', flat=True
                )
            )
            self.assertEqual(fresh, {'bounty', 'beacon'})
            self.assertFalse(
                CachedFeedSnapshot.objects.filter(product='harbor').exists()
            )
        finally:
            release.set()
            time.sleep(0.2)


@override_settings(KEEL_FLEET_PRODUCTS=FAKE_FLEET, HELM_FEED_API_KEY='k', DEMO_MODE=False)
class FetchFeedsCircuitBreakerTests(TransactionTestCase):
    def test_opens_after_threshold(self):
        with mock.patch(
            'keel.feed.client.fetch_product_feed', return_value=_fail('nope')
        ):
            for _ in range(3):
                call_command(
                    'fetch_feeds', 'harbor', '--failure-threshold=3',
                    '--cooldown=60', stdout=StringIO(),
                )

        snap = CachedFeedSnapshot.objects.get(product='harbor')
        self.assertEqual(snap.consecutive_failures, 3)
        self.assertIsNotNone(snap.circuit_open_until)
        self.assertTrue(snap.circuit_open_until > timezone.now())

    def test_skips_fetch_when_open(self):
        CachedFeedSnapshot.objects.create(
            product='harbor',
            feed_data={},
            fetched_at=timezone.now(),
            consecutive_failures=5,
            circuit_open_until=timezone.now() + datetime.timedelta(seconds=300),
            is_stale=True,
            last_error='previously broken',
        )

        with mock.patch(
            'keel.feed.client.fetch_product_feed', return_value=_ok()
        ) as m:
            out = StringIO()
            call_command('fetch_feeds', 'harbor', stdout=out)

        m.assert_not_called()
        self.assertIn('circuit open', out.getvalue())

    def test_ignore_circuit_forces_retry(self):
        CachedFeedSnapshot.objects.create(
            product='harbor',
            feed_data={},
            fetched_at=timezone.now(),
            consecutive_failures=5,
            circuit_open_until=timezone.now() + datetime.timedelta(seconds=300),
            is_stale=True,
            last_error='previously broken',
        )

        with mock.patch(
            'keel.feed.client.fetch_product_feed', return_value=_ok()
        ) as m:
            call_command(
                'fetch_feeds', 'harbor', '--ignore-circuit', stdout=StringIO()
            )

        m.assert_called_once()
        snap = CachedFeedSnapshot.objects.get(product='harbor')
        self.assertEqual(snap.consecutive_failures, 0)
        self.assertIsNone(snap.circuit_open_until)
        self.assertFalse(snap.is_stale)

    def test_success_resets_breaker(self):
        CachedFeedSnapshot.objects.create(
            product='harbor',
            feed_data={},
            fetched_at=timezone.now(),
            consecutive_failures=2,
            circuit_open_until=None,
            is_stale=True,
            last_error='flaky',
        )

        with mock.patch(
            'keel.feed.client.fetch_product_feed', return_value=_ok()
        ):
            call_command('fetch_feeds', 'harbor', stdout=StringIO())

        snap = CachedFeedSnapshot.objects.get(product='harbor')
        self.assertEqual(snap.consecutive_failures, 0)
        self.assertEqual(snap.last_error, '')

    def test_closed_circuit_below_threshold(self):
        with mock.patch(
            'keel.feed.client.fetch_product_feed', return_value=_fail()
        ):
            call_command(
                'fetch_feeds', 'harbor', '--failure-threshold=3',
                stdout=StringIO(),
            )

        snap = CachedFeedSnapshot.objects.get(product='harbor')
        self.assertEqual(snap.consecutive_failures, 1)
        self.assertIsNone(snap.circuit_open_until)
