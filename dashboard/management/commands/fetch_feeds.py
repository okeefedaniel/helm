"""Fetch live product feeds and cache them in CachedFeedSnapshot.

Usage:
    python manage.py fetch_feeds              # all products
    python manage.py fetch_feeds harbor       # single product
    python manage.py fetch_feeds --parallel   # concurrent fetching

In DEMO_MODE with no HELM_FEED_API_KEY, falls back to ``seed_helm``
demo data so the dashboard is never blank.
"""
import concurrent.futures
import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from dashboard.models import CachedFeedSnapshot

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fetch helm-feed data from all fleet products and cache locally'

    def add_arguments(self, parser):
        parser.add_argument(
            'products',
            nargs='*',
            help='Specific product keys to fetch (default: all)',
        )
        parser.add_argument(
            '--parallel',
            action='store_true',
            help='Fetch all products concurrently',
        )
        parser.add_argument(
            '--timeout',
            type=int,
            default=15,
            help='HTTP read timeout in seconds (default: 15)',
        )

    def handle(self, *args, **options):
        from keel.feed.client import fetch_product_feed

        fleet = getattr(settings, 'FLEET_PRODUCTS', [])
        api_key = getattr(settings, 'HELM_FEED_API_KEY', '') or ''
        demo_mode = getattr(settings, 'DEMO_MODE', False)

        # In demo mode without an API key, fall back to seed data
        if demo_mode and not api_key:
            self.stdout.write(self.style.WARNING(
                'DEMO_MODE active with no HELM_FEED_API_KEY — '
                'falling back to seed_helm demo data.'
            ))
            from django.core.management import call_command
            call_command('seed_helm', stdout=self.stdout)
            return

        # Filter to requested products
        requested = set(options['products']) if options['products'] else None
        products_to_fetch = []
        for product in fleet:
            feed_url = product.get('feed_url', '')
            if not feed_url:
                continue
            if requested and product['key'] not in requested:
                continue
            products_to_fetch.append(product)

        if not products_to_fetch:
            self.stdout.write(self.style.WARNING('No products to fetch.'))
            return

        timeout = (5, options['timeout'])
        now = timezone.now()

        if options['parallel']:
            self._fetch_parallel(products_to_fetch, api_key, timeout, now)
        else:
            self._fetch_sequential(products_to_fetch, api_key, timeout, now)

    def _fetch_sequential(self, products, api_key, timeout, now):
        from keel.feed.client import fetch_product_feed

        for product in products:
            self._fetch_one(product, api_key, timeout, now)

    def _fetch_parallel(self, products, api_key, timeout, now):
        from keel.feed.client import fetch_product_feed

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(self._fetch_one, p, api_key, timeout, now): p
                for p in products
            }
            concurrent.futures.wait(futures)

    def _fetch_one(self, product, api_key, timeout, now):
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
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ {key} ({result["duration_ms"]}ms)'
            ))
        else:
            # Mark existing snapshot as stale, record error
            updated = CachedFeedSnapshot.objects.filter(product=key).update(
                is_stale=True,
                last_error=result['error'],
            )
            if not updated:
                # No existing snapshot — create a placeholder
                CachedFeedSnapshot.objects.update_or_create(
                    product=key,
                    defaults={
                        'feed_data': {
                            'product': key,
                            'product_label': product['label'],
                            'product_url': product.get('url', ''),
                            'metrics': [],
                            'action_items': [],
                            'alerts': [],
                            'sparklines': {},
                        },
                        'fetched_at': now,
                        'fetch_duration_ms': result['duration_ms'],
                        'is_stale': True,
                        'last_error': result['error'],
                    },
                )
            self.stdout.write(self.style.ERROR(
                f'  ✗ {key}: {result["error"][:100]}'
            ))
