"""Query helpers for cross-app consumers (e.g. the dashboard).

Keeping these out of views.py so importing them does not pull in the
view layer's middleware/decorator surface.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Task


def _my_open_tasks_qs(user):
    """Tasks where the user is the assignee or an active collaborator,
    excluding DONE. The same predicate used by /tasks/ — kept
    in one place so the dashboard and the task list can never drift.
    """
    return (
        Task.objects
        .filter(Q(assignee=user) | Q(collaborators__user=user))
        .exclude(status=Task.Status.DONE)
        .distinct()
        .select_related('project', 'assignee')
    )


def get_user_deadline_rail(user, weeks_ahead: int = 2):
    """Group the user's open tasks into deadline buckets for the dashboard.

    Returns a dict with four keys, each mapping to a list of Task rows:

    - ``overdue``   — due_date strictly before today, status != DONE
    - ``today``     — due_date == today
    - ``this_week`` — due_date in (today, today+7d]
    - ``upcoming``  — due_date in (today+7d, today+weeks_ahead*7d]

    Tasks with no due_date are omitted (they appear in /tasks/
    instead). The dashboard separately surfaces a "no due date" count
    via :func:`get_user_undated_count`.

    Each bucket is sorted by due_date ascending, then by Task.position.
    """
    today = timezone.localdate()
    horizon = today + timedelta(weeks=weeks_ahead)
    qs = (
        _my_open_tasks_qs(user)
        .filter(due_date__isnull=False, due_date__lte=horizon)
        .order_by('due_date', 'position')
    )

    buckets = {'overdue': [], 'today': [], 'this_week': [], 'upcoming': []}
    week_end = today + timedelta(days=7)
    for task in qs:
        if task.due_date < today:
            buckets['overdue'].append(task)
        elif task.due_date == today:
            buckets['today'].append(task)
        elif task.due_date <= week_end:
            buckets['this_week'].append(task)
        else:
            buckets['upcoming'].append(task)
    return buckets


def get_user_project_deadline_rail(user, weeks_ahead: int = 2):
    """Project-grouped variant of the deadline rail.

    Same buckets as :func:`get_user_deadline_rail`, but aggregated by
    parent project. Each bucket is a list of dicts:

        {'project': Project, 'soonest_task': Task,
         'total_count': int, 'has_overdue': bool}

    A project lands in the most-urgent bucket among the user's open
    dated tasks on it (overdue > today > this_week > upcoming).
    ``soonest_task`` is the one driving urgency (min due_date).
    ``total_count`` rolls up every dated open task the user has on the
    project across all four buckets.
    """
    today = timezone.localdate()
    horizon = today + timedelta(weeks=weeks_ahead)
    week_end = today + timedelta(days=7)
    qs = (
        _my_open_tasks_qs(user)
        .filter(due_date__isnull=False, due_date__lte=horizon)
        .order_by('due_date', 'position')
    )

    by_project = {}
    for task in qs:
        if task.due_date < today:
            bucket = 'overdue'
        elif task.due_date == today:
            bucket = 'today'
        elif task.due_date <= week_end:
            bucket = 'this_week'
        else:
            bucket = 'upcoming'
        entry = by_project.get(task.project_id)
        if entry is None:
            by_project[task.project_id] = {
                'project': task.project,
                'soonest_task': task,
                'bucket': bucket,
                'total_count': 1,
                'has_overdue': bucket == 'overdue',
            }
        else:
            entry['total_count'] += 1
            if bucket == 'overdue':
                entry['has_overdue'] = True

    buckets = {'overdue': [], 'today': [], 'this_week': [], 'upcoming': []}
    for entry in by_project.values():
        buckets[entry['bucket']].append(entry)
    return buckets


def get_user_undated_count(user) -> int:
    """Count of the user's open tasks with no due_date."""
    return _my_open_tasks_qs(user).filter(due_date__isnull=True).count()


def get_user_open_task_count(user) -> int:
    """Total open task count for the user (any due_date or none)."""
    return _my_open_tasks_qs(user).count()
