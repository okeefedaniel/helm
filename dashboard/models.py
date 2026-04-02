"""Helm dashboard models.

Helm's own data model is minimal. Almost everything is read from Keel
or other products via cached feed snapshots.
"""
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from keel.core.models import KeelBaseModel


class DashboardBookmark(KeelBaseModel):
    """User-created bookmarks to specific items across the fleet.

    Example: "Watch Grant #1234" or "Track SB-5 in Lookout"
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='helm_bookmarks',
    )
    product = models.CharField(max_length=50)
    item_type = models.CharField(max_length=50)
    item_id = models.CharField(max_length=100)
    item_label = models.CharField(max_length=200)
    deep_link = models.URLField()
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ['user', 'product', 'item_id']
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.product}: {self.item_label}'


class BriefingPreference(KeelBaseModel):
    """Per-user preferences for the DISPATCH morning briefing.

    Controls what the /api/v1/briefing/ endpoint includes.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='helm_briefing_prefs',
    )
    include_metrics = models.JSONField(
        default=list,
        help_text=_('Product keys to include in the briefing, e.g. ["harbor", "purser"]'),
    )
    include_action_items = models.BooleanField(default=True)
    include_alerts = models.BooleanField(default=True)
    include_activity_feed = models.BooleanField(default=False)
    alert_severity_threshold = models.CharField(
        max_length=20,
        choices=[
            ('critical', _('Critical only')),
            ('warning', _('Warning and above')),
            ('info', _('All')),
        ],
        default='warning',
    )
    briefing_time = models.TimeField(default='06:30')
    timezone = models.CharField(max_length=50, default='America/New_York')

    def __str__(self):
        return f'Briefing prefs: {self.user}'


class CachedFeedSnapshot(KeelBaseModel):
    """Cached copy of a product's helm-feed response.

    Helm caches feed responses to avoid hitting every product on
    every page load. Cache is refreshed on a configurable interval
    (default: 5 minutes). The cache is the read source for the
    dashboard; products are the write source.
    """
    product = models.CharField(max_length=50, unique=True)
    feed_data = models.JSONField()
    fetched_at = models.DateTimeField()
    fetch_duration_ms = models.IntegerField(default=0)
    is_stale = models.BooleanField(default=False)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ['product']

    def __str__(self):
        stale = ' [STALE]' if self.is_stale else ''
        return f'{self.product}{stale} — fetched {self.fetched_at}'


class UserDashboardLayout(KeelBaseModel):
    """Per-user widget arrangement preferences."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='helm_dashboard_layout',
    )
    layout = models.JSONField(
        default=dict,
        help_text=_('Widget arrangement: {"widgets": [...], "collapsed": [...]}'),
    )

    def __str__(self):
        return f'Layout: {self.user}'
