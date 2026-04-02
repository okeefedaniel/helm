"""Dashboard services — feed aggregation and data access."""
import logging

from django.conf import settings

from .feed_contract import ProductFeed
from .models import CachedFeedSnapshot

logger = logging.getLogger(__name__)

# Product metadata keyed by product key
PRODUCT_META = {p['key']: p for p in getattr(settings, 'FLEET_PRODUCTS', [])}


class FeedAggregator:
    """Reads cached feed snapshots and assembles dashboard data."""

    def get_all_feeds(self) -> list[ProductFeed]:
        """Return all cached product feeds as ProductFeed objects."""
        feeds = []
        for snapshot in CachedFeedSnapshot.objects.all():
            try:
                feed = ProductFeed.from_dict(snapshot.feed_data)
                feeds.append(feed)
            except (KeyError, TypeError) as e:
                logger.warning('Invalid feed data for %s: %s', snapshot.product, e)
        return feeds

    def get_feed(self, product_key: str) -> ProductFeed | None:
        """Return a single product's cached feed."""
        try:
            snapshot = CachedFeedSnapshot.objects.get(product=product_key)
            return ProductFeed.from_dict(snapshot.feed_data)
        except CachedFeedSnapshot.DoesNotExist:
            return None

    def get_all_action_items(self) -> list[dict]:
        """Aggregate action items from all products, sorted by priority."""
        priority_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        items = []
        for feed in self.get_all_feeds():
            meta = PRODUCT_META.get(feed.product, {})
            for item in feed.action_items:
                item_data = item.__dict__ if hasattr(item, '__dict__') else item
                entry = {
                    'product': feed.product,
                    'product_label': feed.product_label,
                    'product_icon': meta.get('icon', 'bi-app'),
                }
                entry.update(item_data)
                items.append(entry)
        items.sort(key=lambda x: (
            priority_order.get(x.get('priority', 'medium'), 2),
            x.get('due_date', '') or 'z',
        ))
        return items

    def get_all_alerts(self) -> list[dict]:
        """Aggregate alerts from all products, sorted by severity."""
        severity_order = {'critical': 0, 'warning': 1, 'info': 2}
        alerts = []
        for feed in self.get_all_feeds():
            meta = PRODUCT_META.get(feed.product, {})
            for alert in feed.alerts:
                alert_data = alert.__dict__ if hasattr(alert, '__dict__') else alert
                entry = {
                    'product': feed.product,
                    'product_label': feed.product_label,
                    'product_icon': meta.get('icon', 'bi-app'),
                }
                entry.update(alert_data)
                alerts.append(entry)
        alerts.sort(key=lambda x: severity_order.get(x.get('severity', 'info'), 2))
        return alerts

    def get_metrics_by_product(self) -> dict[str, dict]:
        """Return metrics grouped by product, with product metadata."""
        result = {}
        for feed in self.get_all_feeds():
            meta = PRODUCT_META.get(feed.product, {})
            result[feed.product] = {
                'product': feed.product,
                'product_label': feed.product_label,
                'product_url': feed.product_url,
                'product_icon': meta.get('icon', 'bi-app'),
                'product_tagline': meta.get('tagline', ''),
                'updated_at': feed.updated_at,
                'is_stale': CachedFeedSnapshot.objects.filter(
                    product=feed.product, is_stale=True
                ).exists(),
                'metrics': [m.__dict__ for m in feed.metrics],
                'sparklines': {
                    k: v.__dict__ for k, v in feed.sparklines.items()
                },
            }
        return result

    def get_fleet_health(self) -> str:
        """Return fleet health status: green, yellow, or red."""
        stale_count = CachedFeedSnapshot.objects.filter(is_stale=True).count()
        total = CachedFeedSnapshot.objects.count()
        if total == 0:
            return 'gray'
        if stale_count == 0:
            return 'green'
        if stale_count <= 2:
            return 'yellow'
        return 'red'

    def get_briefing_data(self, user) -> dict:
        """Build structured briefing for DISPATCH integration."""
        feeds = self.get_all_feeds()
        action_items = self.get_all_action_items()
        alerts = self.get_all_alerts()

        critical_actions = [
            a['title'] for a in action_items
            if a.get('priority') in ('critical', 'high')
        ][:5]

        critical_alerts = [
            a['title'] for a in alerts
            if a.get('severity') == 'critical'
        ][:5]

        metrics_summary = {}
        for feed in feeds:
            parts = []
            for m in feed.metrics[:3]:
                val = m.value if not hasattr(m, 'value') else m.value
                unit = m.unit if hasattr(m, 'unit') else None
                if unit == 'USD':
                    formatted = f'${val:,.0f}'
                else:
                    formatted = f'{val:,}'
                label = m.label if hasattr(m, 'label') else m.key
                parts.append(f'{formatted} {label.lower()}')
            if parts:
                metrics_summary[feed.product] = ', '.join(parts)

        return {
            'briefing_date': '',  # Set by the view
            'fiscal_context': '',  # Set by the view
            'action_items_count': len(action_items),
            'critical_actions': critical_actions,
            'alerts_count': len(alerts),
            'critical_alerts': critical_alerts,
            'metrics_summary': metrics_summary,
            'fleet_health': self.get_fleet_health(),
        }
