from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = 'dashboard'
    verbose_name = 'Helm Dashboard'

    def ready(self):
        # Pre-import scheduled-job command modules so the @scheduled_job
        # decorator fires at app load time (before sync_scheduled_jobs runs
        # at deploy startup). Without this, Django only imports a command
        # module when it's first invoked, leaving the registry empty.
        from dashboard.management.commands import fetch_feeds  # noqa: F401
