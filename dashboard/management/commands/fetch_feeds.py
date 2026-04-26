"""Fetch live product feeds and cache them in CachedFeedSnapshot.

Usage:
    python manage.py fetch_feeds              # all products, parallel
    python manage.py fetch_feeds harbor       # single product
    python manage.py fetch_feeds --sequential # disable concurrency (debugging)

Reliability:
- Parallel by default: 8 fleet products fetch concurrently so the total
  time is bounded by the slowest single product, not the sum.
- Wall-clock budget: `--overall-timeout` caps the whole run. Stragglers
  are abandoned (their threads keep going but the command returns).
- Circuit breaker: a product that fails ``--failure-threshold`` times
  in a row is skipped for ``--cooldown`` seconds so one broken product
  doesn't burn the per-fetch timeout on every cron run.

In DEMO_MODE with no HELM_FEED_API_KEY, falls back to ``seed_helm``
demo data so the dashboard is never blank.
"""
import concurrent.futures
import datetime
import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from keel.scheduling import scheduled_job

from dashboard.models import CachedFeedSnapshot

logger = logging.getLogger(__name__)

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 300
DEFAULT_OVERALL_TIMEOUT_SECONDS = 30
DEFAULT_PER_FETCH_TIMEOUT_SECONDS = 15
MAX_WORKERS = 8


@scheduled_job(
    slug='helm-fetch-feeds',
    name='Helm — Fetch peer product feeds',
    cron='*/15 * * * *',
    owner='helm',
    description=(
        'Pulls /api/v1/helm-feed/ from each fleet product and refreshes '
        'CachedFeedSnapshot rows. Parallel + circuit-breaker out of the box.'
    ),
    notes='Fired by .github/workflows/cron.yml every 15 minutes UTC.',
    timeout_minutes=5,
)
class Command(BaseCommand):
    help = 'Fetch helm-feed data from all fleet products and cache locally'

    def add_arguments(self, parser):
        parser.add_argument(
            'products',
            nargs='*',
            help='Specific product keys to fetch (default: all)',
        )
        parser.add_argument(
            '--sequential',
            action='store_true',
            help='Fetch products one at a time (default: parallel)',
        )
        parser.add_argument(
            '--timeout',
            type=int,
            default=DEFAULT_PER_FETCH_TIMEOUT_SECONDS,
            help=f'HTTP read timeout in seconds per product (default: {DEFAULT_PER_FETCH_TIMEOUT_SECONDS})',
        )
        parser.add_argument(
            '--overall-timeout',
            type=int,
            default=DEFAULT_OVERALL_TIMEOUT_SECONDS,
            help=f'Wall-clock budget for the whole run (default: {DEFAULT_OVERALL_TIMEOUT_SECONDS})',
        )
        parser.add_argument(
            '--failure-threshold',
            type=int,
            default=DEFAULT_FAILURE_THRESHOLD,
            help=f'Open circuit after N consecutive failures (default: {DEFAULT_FAILURE_THRESHOLD})',
        )
        parser.add_argument(
            '--cooldown',
            type=int,
            default=DEFAULT_COOLDOWN_SECONDS,
            help=f'Circuit-open duration in seconds (default: {DEFAULT_COOLDOWN_SECONDS})',
        )
        parser.add_argument(
            '--ignore-circuit',
            action='store_true',
            help='Ignore circuit breaker state (force-retry even if open)',
        )

    def handle(self, *args, **options):
        fleet = getattr(settings, 'FLEET_PRODUCTS', [])
        api_key = getattr(settings, 'HELM_FEED_API_KEY', '') or ''
        demo_mode = getattr(settings, 'DEMO_MODE', False)

        if demo_mode and not api_key:
            self.stdout.write(self.style.WARNING(
                'DEMO_MODE active with no HELM_FEED_API_KEY — '
                'falling back to seed_helm demo data.'
            ))
            from django.core.management import call_command
            call_command('seed_helm', stdout=self.stdout)
            return

        requested = set(options['products']) if options['products'] else None
        candidates = []
        for product in fleet:
            feed_url = product.get('feed_url', '')
            if not feed_url:
                continue
            if requested and product['key'] not in requested:
                continue
            candidates.append(product)

        if not candidates:
            self.stdout.write(self.style.WARNING('No products to fetch.'))
            return

        now = timezone.now()
        products_to_fetch, skipped = self._apply_circuit_breaker(
            candidates, now, options['ignore_circuit']
        )
        for product, open_until in skipped:
            remaining = int((open_until - now).total_seconds())
            self.stdout.write(self.style.WARNING(
                f'  ⊘ {product["key"]}: circuit open ({remaining}s left)'
            ))

        if not products_to_fetch:
            self.stdout.write(self.style.WARNING('All products circuit-broken.'))
            return

        timeout = (5, options['timeout'])
        config = {
            'failure_threshold': options['failure_threshold'],
            'cooldown': options['cooldown'],
        }

        if options['sequential']:
            self._fetch_sequential(products_to_fetch, api_key, timeout, now, config)
        else:
            self._fetch_parallel(
                products_to_fetch, api_key, timeout, now, config,
                options['overall_timeout'],
            )

    def _apply_circuit_breaker(self, products, now, ignore):
        """Partition candidates into (to_fetch, skipped) using DB state."""
        if ignore:
            return list(products), []
        keys = [p['key'] for p in products]
        snapshots = {
            s.product: s for s in CachedFeedSnapshot.objects.filter(product__in=keys)
        }
        to_fetch, skipped = [], []
        for product in products:
            snap = snapshots.get(product['key'])
            if snap and snap.circuit_open_until and snap.circuit_open_until > now:
                skipped.append((product, snap.circuit_open_until))
            else:
                to_fetch.append(product)
        return to_fetch, skipped

    def _fetch_sequential(self, products, api_key, timeout, now, config):
        for product in products:
            self._fetch_one(product, api_key, timeout, now, config)

    def _fetch_parallel(self, products, api_key, timeout, now, config, overall_timeout):
        workers = min(MAX_WORKERS, len(products))
        # Don't use a `with` block: its __exit__ calls shutdown(wait=True),
        # which would block the command on the slowest straggler and defeat
        # the whole point of --overall-timeout.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {
                executor.submit(
                    self._fetch_one, p, api_key, timeout, now, config
                ): p
                for p in products
            }
            done, not_done = concurrent.futures.wait(
                futures, timeout=overall_timeout
            )
            for future in not_done:
                product = futures[future]
                self.stdout.write(self.style.ERROR(
                    f'  ⏱ {product["key"]}: abandoned after {overall_timeout}s overall budget'
                ))
        finally:
            # Abandoned threads keep running to completion in the background;
            # they can't be forcibly killed. Their update_or_create write is
            # idempotent, so a late write is harmless.
            executor.shutdown(wait=False, cancel_futures=True)

    def _fetch_one(self, product, api_key, timeout, now, config):
        from keel.feed.client import fetch_product_feed

        key = product['key']
        feed_url = product['feed_url']

        self.stdout.write(f'  Fetching {key} from {feed_url} ...')
        result = fetch_product_feed(feed_url, api_key, timeout=timeout)

        if result['ok']:
            CachedFeedSnapshot.objects.update_or_create(
                product=key,
                defaults={
                    'feed_data': result['data'],
                    'fetched_at': now,
                    'fetch_duration_ms': result['duration_ms'],
                    'is_stale': False,
                    'last_error': '',
                    'consecutive_failures': 0,
                    'circuit_open_until': None,
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ {key} ({result["duration_ms"]}ms)'
            ))
            return

        self._record_failure(product, result, now, config)

    def _record_failure(self, product, result, now, config):
        key = product['key']
        existing = CachedFeedSnapshot.objects.filter(product=key).first()
        failures = (existing.consecutive_failures if existing else 0) + 1
        circuit_open_until = None
        if failures >= config['failure_threshold']:
            circuit_open_until = now + datetime.timedelta(seconds=config['cooldown'])

        if existing:
            existing.is_stale = True
            existing.last_error = result['error']
            existing.consecutive_failures = failures
            existing.circuit_open_until = circuit_open_until
            existing.save(update_fields=[
                'is_stale', 'last_error', 'consecutive_failures',
                'circuit_open_until', 'updated_at',
            ])
        else:
            CachedFeedSnapshot.objects.create(
                product=key,
                feed_data={
                    'product': key,
                    'product_label': product['label'],
                    'product_url': product.get('url', ''),
                    'metrics': [],
                    'action_items': [],
                    'alerts': [],
                    'sparklines': {},
                },
                fetched_at=now,
                fetch_duration_ms=result['duration_ms'],
                is_stale=True,
                last_error=result['error'],
                consecutive_failures=failures,
                circuit_open_until=circuit_open_until,
            )

        suffix = (
            f' (circuit OPEN for {config["cooldown"]}s after {failures} failures)'
            if circuit_open_until else f' (failure {failures})'
        )
        self.stdout.write(self.style.ERROR(
            f'  ✗ {key}: {result["error"][:100]}{suffix}'
        ))
