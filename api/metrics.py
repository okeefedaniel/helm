"""Operational metrics endpoint at /api/v1/metrics/.

Exposes lightweight counters + gauges for monitoring tools (BetterUptime,
Pingdom, cron-job.org parsers, Boswell daily check, etc.) to detect
silent regressions like the 2026-03-26 → 2026-04-25 audit-log gap.

The shape is intentionally simple JSON — no Prometheus exposition
format, no labels-as-strings — so curl + jq is enough to parse and
alert. If/when we adopt a real metrics backend, this endpoint becomes
the data source.

Counters that would have caught known regressions:
- ``audit_log_writes_24h`` — 0 means audit logging is broken
  (would have caught the gap on day 1).
- ``notifications_sent_24h`` — 0 with PM activity means the
  notification pipeline is broken.
- ``scheduled_runs_24h`` — 0 means cron isn't firing.
- ``scheduled_failures_24h`` — non-zero means a scheduled job is
  erroring.
"""
import hmac
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.http import JsonResponse
from django.utils import timezone


def _has_metrics_token(request) -> bool:
    """Return True iff request carries a valid HELM_METRICS_TOKEN bearer."""
    expected = getattr(settings, 'HELM_METRICS_TOKEN', '') or ''
    if not expected:
        return False
    auth = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth.startswith('Bearer '):
        return False
    return hmac.compare_digest(auth[7:].strip(), expected)


def _safe_count(model_path, **filters):
    """Return a count, or None if the model isn't installed."""
    from django.apps import apps
    try:
        Model = apps.get_model(model_path)
    except (LookupError, ValueError):
        return None
    try:
        return Model.objects.filter(**filters).count()
    except Exception:
        return None


def metrics(request):
    """Return JSON snapshot of operational counters.

    Auth: either staff session (browser) OR ``Authorization: Bearer
    $HELM_METRICS_TOKEN`` (external pollers like cron-job.org). Without
    the token bypass, no external monitoring can reach this canary.
    """
    if not _has_metrics_token(request):
        return _staff_only(request)
    return _render(request)


@staff_member_required
def _staff_only(request):
    return _render(request)


def _render(request):
    now = timezone.now()
    last_24h = now - timedelta(hours=24)
    last_1h = now - timedelta(hours=1)

    payload = {
        'generated_at': now.isoformat(),
        'window': {'last_24h': last_24h.isoformat(), 'last_1h': last_1h.isoformat()},
    }

    # Audit log — the canary. Zero writes = silent regression.
    payload['audit_log_writes_total'] = _safe_count('helm_core.AuditLog')
    payload['audit_log_writes_24h'] = _safe_count(
        'helm_core.AuditLog', timestamp__gte=last_24h,
    )
    payload['audit_log_writes_1h'] = _safe_count(
        'helm_core.AuditLog', timestamp__gte=last_1h,
    )

    # Notifications — both directions of the canary.
    payload['notifications_sent_total'] = _safe_count('helm_core.Notification')
    payload['notifications_sent_24h'] = _safe_count(
        'helm_core.Notification', created_at__gte=last_24h,
    )
    notifications_failed_24h = _safe_count(
        'helm_core.NotificationLog',
        created_at__gte=last_24h, success=False,
    )
    payload['notifications_failed_24h'] = notifications_failed_24h

    # Project lifecycle gauges (only meaningful when HELM_TASKS_ENABLED).
    payload['projects_active'] = _safe_count(
        'helm_tasks.Project', status='active',
    )
    payload['projects_on_hold'] = _safe_count(
        'helm_tasks.Project', status='on_hold',
    )
    payload['projects_completed'] = _safe_count(
        'helm_tasks.Project', status='completed',
    )
    payload['projects_archived'] = _safe_count(
        'helm_tasks.Project', status='archived',
    )

    # Task counts by status.
    task_buckets = {}
    try:
        from django.apps import apps
        Task = apps.get_model('helm_tasks.Task')
        for row in Task.objects.values('status').annotate(n=Count('id')):
            task_buckets[row['status']] = row['n']
    except (LookupError, ValueError, Exception):
        task_buckets = None
    payload['tasks_by_status'] = task_buckets

    # Project transitions (24h).
    payload['project_transitions_24h'] = _safe_count(
        'helm_tasks.ProjectStatusHistory', changed_at__gte=last_24h,
    )
    payload['task_transitions_24h'] = _safe_count(
        'helm_tasks.TaskStatusHistory', changed_at__gte=last_24h,
    )

    # Scheduling — surfaces silent cron failures.
    scheduled_runs_24h = _safe_count(
        'keel_scheduling.CommandRun', started_at__gte=last_24h,
    )
    scheduled_failures_24h = _safe_count(
        'keel_scheduling.CommandRun',
        started_at__gte=last_24h, status='error',
    )
    payload['scheduled_runs_24h'] = scheduled_runs_24h
    payload['scheduled_failures_24h'] = scheduled_failures_24h

    # FOIA queue depth — pending review backlog.
    payload['foia_export_pending'] = _safe_count(
        'helm_core.FOIAExportItem', review_status='pending',
    )

    # Health flags — admins can alert on these directly without parsing
    # the rest. True = something looks wrong.
    flags = {
        'audit_silent_24h': (
            payload['audit_log_writes_24h'] is not None
            and payload['audit_log_writes_24h'] == 0
        ),
        'cron_silent_24h': (
            scheduled_runs_24h is not None and scheduled_runs_24h == 0
        ),
        'cron_failures_24h': (
            scheduled_failures_24h is not None and scheduled_failures_24h > 0
        ),
        'notifications_failing': (
            notifications_failed_24h is not None and notifications_failed_24h > 0
        ),
    }
    payload['flags'] = flags
    payload['healthy'] = not any(flags.values())

    return JsonResponse(payload)
