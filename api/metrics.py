"""Operational metrics endpoint at /api/v1/metrics/.

Thin wrapper over ``keel.ops.canary`` — keel owns the four core flags
(``audit_silent_24h``, ``cron_silent_24h``, ``cron_failures_24h``,
``notifications_failing``), and helm bolts on its own product-specific
gauges (project lifecycle, task buckets, FOIA queue depth) via the
``extras_callable`` hook.

Auth: staff session OR ``Authorization: Bearer $KEEL_METRICS_TOKEN``
(read from the ``HELM_METRICS_TOKEN`` env var in helm — the GH Actions
canary at ``.github/workflows/canary.yml`` polls this every 15min).
"""
from django.db.models import Count

from keel.ops.canary import _safe_count
from keel.ops.views import canary_view


def _helm_extras(now, last_24h, last_1h, **_):
    """Helm-specific gauges: projects, tasks, FOIA queue."""
    extras = {
        'projects_active': _safe_count('helm_tasks.Project', status='active'),
        'projects_on_hold': _safe_count('helm_tasks.Project', status='on_hold'),
        'projects_completed': _safe_count('helm_tasks.Project', status='completed'),
        'projects_archived': _safe_count('helm_tasks.Project', status='archived'),
        'project_transitions_24h': _safe_count(
            'helm_tasks.ProjectStatusHistory', changed_at__gte=last_24h,
        ),
        'task_transitions_24h': _safe_count(
            'helm_tasks.TaskStatusHistory', changed_at__gte=last_24h,
        ),
        'foia_export_pending': _safe_count(
            'helm_core.FOIAExportItem', review_status='pending',
        ),
    }

    # Task buckets keyed by status. Done as a single grouped query.
    task_buckets = {}
    try:
        from django.apps import apps
        Task = apps.get_model('helm_tasks.Task')
        for row in Task.objects.values('status').annotate(n=Count('id')):
            task_buckets[row['status']] = row['n']
    except (LookupError, ValueError, Exception):
        task_buckets = None
    extras['tasks_by_status'] = task_buckets
    return extras


metrics = canary_view(extras_callable=_helm_extras)
