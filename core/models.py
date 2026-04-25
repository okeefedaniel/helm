"""Helm-specific concrete subclasses of Keel abstract models."""
from django.db import models

from keel.core.models import AbstractAuditLog, AbstractNotification
from keel.foia.models import AbstractFOIAExportItem
from keel.notifications.models import AbstractNotificationPreference, AbstractNotificationLog


class AuditLog(AbstractAuditLog):
    """Helm audit log."""

    # Helm-specific action constants
    ACTION_BRIEFING_GENERATED = 'briefing_generated'
    ACTION_FEED_REFRESH = 'feed_refresh'
    ACTION_BOOKMARK_CREATED = 'bookmark_created'
    ACTION_LAYOUT_UPDATED = 'layout_updated'

    action = models.CharField(max_length=25)

    class Meta(AbstractAuditLog.Meta):
        db_table = 'helm_audit_log'


class Notification(AbstractNotification):

    class Meta(AbstractNotification.Meta):
        db_table = 'helm_notification'


class NotificationPreference(AbstractNotificationPreference):

    class Meta(AbstractNotificationPreference.Meta):
        db_table = 'helm_notification_preference'


class NotificationLog(AbstractNotificationLog):

    class Meta(AbstractNotificationLog.Meta):
        db_table = 'helm_notification_log'


class FOIAExportItem(AbstractFOIAExportItem):
    """Helm's concrete FOIA export queue. Receives Project / ProjectNote /
    ProjectAttachment records pushed via keel.foia.export.submit_to_foia."""

    class Meta(AbstractFOIAExportItem.Meta):
        db_table = 'helm_foia_export_item'
