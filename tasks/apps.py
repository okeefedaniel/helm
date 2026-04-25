from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tasks'
    label = 'helm_tasks'
    verbose_name = 'Helm Tasks'

    def ready(self):
        # Register the 12 PM lifecycle notification types in the keel
        # registry so notify() calls in services.py resolve them.
        from tasks.notifications import register_all as register_notifications
        register_notifications()
        # Register Helm's FOIA-exportable record types (Project, ProjectNote,
        # ProjectAttachment) with keel.foia.export.foia_export_registry so
        # Admiralty can pull them via the cross-product FOIA queue.
        from tasks.foia import register_all as register_foia
        register_foia()
        # Connect the TaskComment post_save signal that fires the
        # helm_task_comment_added notification.
        from tasks import signals  # noqa: F401
        # Pre-import scheduled-job command modules so the @scheduled_job
        # decorator fires at app load time (before sync_scheduled_jobs runs
        # at deploy startup). Without this, Django only imports a command
        # module when it's first invoked, leaving the registry empty.
        from tasks.management.commands import notify_due_tasks  # noqa: F401
        # Register Helm's calendar event types with keel.calendar.
        from tasks.calendar_events import register_calendar_event_types
        register_calendar_event_types()
