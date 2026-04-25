from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tasks'
    label = 'helm_tasks'
    verbose_name = 'Helm Tasks'

    def ready(self):
        # Register the 12 PM lifecycle notification types in the keel
        # registry so notify() calls in services.py resolve them.
        from tasks.notifications import register_all
        register_all()
        # Connect the TaskComment post_save signal that fires the
        # helm_task_comment_added notification.
        from tasks import signals  # noqa: F401
