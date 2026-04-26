"""Calendar UI + JSON feed + iCal export for Helm Project Management.

Three endpoints:

- ``calendar_index`` (GET /tasks/calendar/) — page hosting FullCalendar 6
  via CDN. Single HTML page; the JS pulls events from the JSON feed.
- ``calendar_events_json`` (GET /tasks/calendar/events.json) — FullCalendar's
  JSON event feed. Filters through ``Project.objects.visible_to(user)`` so
  users only see what they can access. Returns project target ends,
  project completed dates, and open task due dates.
- ``calendar_ical`` (GET /tasks/calendar.ics) — login-required iCal export
  via ``keel.calendar.ical.generate_ical()``. Same event set as the JSON
  feed but emitted as RFC 5545 VEVENT. Tokenized public subscription is
  intentionally NOT supported (per plan §12.5 — login-only for v1).

Color scheme:
- Task open + on-time:    blue   #3b82f6
- Task overdue:           red    #dc2626
- Task blocked:           amber  #f59e0b
- Project target end:     gold   #fbbf24
- Project completed:      green  #10b981
- FOIA project deadline:  red    #dc2626 (border)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone as dt_tz
from typing import Optional

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from keel.calendar import generate_ical
from keel.calendar.ical import ical_response

from tasks.foia import urgency_tier
from tasks.models import Project, Task


# FOIA urgency tier → FullCalendar color mapping. Mirrors the
# _foia_clock.html partial so the badge and the calendar event read the
# same color for the same project.
_FOIA_TIER_COLOR = {
    'overdue': '#dc2626',  # red
    'urgent': '#dc2626',   # red
    'warning': '#ea580c',  # orange
    'caution': '#f59e0b',  # amber
    'tolled': '#6b7280',   # gray
    'ok': '#10b981',       # green
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@dataclass
class _ICalEvent:
    """Adapter object for keel.calendar.generate_ical(events).

    The keel API expects objects with id, title, start_time, end_time,
    location, description, all_day attributes.
    """
    id: str
    title: str
    start_time: datetime
    end_time: datetime
    description: str = ''
    location: str = ''
    all_day: bool = True


def _date_to_dt(d, end: bool = False) -> datetime:
    """Convert a date to a UTC datetime at midnight (or end-of-day)."""
    return datetime.combine(d, time(23, 59) if end else time(0, 0), tzinfo=dt_tz.utc)


def _project_visible_qs(user):
    """Active (non-archived) projects visible to this user."""
    return (Project.objects.visible_to(user)
                            .active()
                            .select_related())


def _task_visible_qs(user):
    """Open tasks under projects this user can see."""
    visible_project_ids = (Project.objects.visible_to(user)
                                          .values_list('id', flat=True))
    return (Task.objects
                .filter(project_id__in=visible_project_ids,
                        due_date__isnull=False)
                .exclude(status=Task.Status.DONE)
                .select_related('project', 'assignee'))


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
@login_required
def calendar_index(request):
    """Calendar page — single HTML hosting FullCalendar 6."""
    return render(request, 'tasks/calendar.html')


@login_required
def calendar_events_json(request):
    """JSON feed for FullCalendar.

    Query params (passed by FullCalendar):
      start, end — ISO datetimes, the visible window (inclusive/exclusive).
    We accept them but don't strictly filter on them — the queryset is
    small enough at suite scale; FullCalendar drops events outside the
    window client-side.
    """
    today = timezone.localdate()
    events = []

    # Projects with target end dates (gold / amber / red depending on FOIA + lateness).
    for p in _project_visible_qs(request.user):
        if p.target_end_at:
            color = '#fbbf24'  # gold
            border = None
            if p.kind == Project.Kind.FOIA:
                # FOIA target ends are statutory deadlines — red border to flag.
                border = '#dc2626'
            elif p.target_end_at < today:
                # Past target end and not completed — late.
                color = '#dc2626'
            events.append({
                'id': f'p-target-{p.pk}',
                'title': f'⛳ {p.name}',
                'start': p.target_end_at.isoformat(),
                'allDay': True,
                'url': p.get_absolute_url(),
                'color': color,
                **({'borderColor': border} if border else {}),
                'extendedProps': {
                    'kind': 'project_target_end',
                    'project_kind': p.kind,
                    'project_status': p.status,
                },
            })
        if p.completed_at:
            events.append({
                'id': f'p-done-{p.pk}',
                'title': f'✓ {p.name}',
                'start': p.completed_at.isoformat(),
                'url': p.get_absolute_url(),
                'color': '#10b981',  # green
                'extendedProps': {'kind': 'project_completed'},
            })
        # FOIA statutory deadlines surface as a separate event colored by
        # urgency_tier — independent of target_end_at, since FOIA projects
        # often only have the statutory deadline set, not target_end_at.
        if p.kind == Project.Kind.FOIA and p.foia_statutory_deadline_at:
            tier = urgency_tier(p, today=today)
            events.append({
                'id': f'p-foia-{p.pk}',
                'title': f'⚖ FOIA: {p.name}',
                'start': p.foia_statutory_deadline_at.isoformat(),
                'allDay': True,
                'url': p.get_absolute_url(),
                'color': _FOIA_TIER_COLOR.get(tier, '#3b82f6'),
                'extendedProps': {
                    'kind': 'foia_statutory_deadline',
                    'urgency_tier': tier,
                    'jurisdiction': p.foia_jurisdiction,
                },
            })

    # Tasks due — color by status / overdue.
    for t in _task_visible_qs(request.user):
        if t.status == Task.Status.BLOCKED:
            color = '#f59e0b'  # amber
        elif t.due_date < today:
            color = '#dc2626'  # red — overdue
        else:
            color = '#3b82f6'  # blue — on time
        events.append({
            'id': f't-{t.pk}',
            'title': t.title,
            'start': t.due_date.isoformat(),
            'allDay': True,
            'url': f'/tasks/projects/{t.project.slug}/',
            'color': color,
            'extendedProps': {
                'kind': 'task_due',
                'task_status': t.status,
                'priority': t.priority,
                'project_slug': t.project.slug,
                'assignee': t.assignee.email if t.assignee else None,
            },
        })

    return JsonResponse({'events': events})


@login_required
def calendar_ical(request):
    """iCal export — same event set as the JSON feed, emitted as VEVENT.

    Login-required (no tokenized public subscription in v1 — per plan
    §12.5). Calendar clients that prompt for credentials will work;
    "subscribe via URL" without auth will not.
    """
    user = request.user
    today = timezone.localdate()

    events: list[_ICalEvent] = []
    for p in _project_visible_qs(user):
        if p.target_end_at:
            label_prefix = 'FOIA Deadline' if p.kind == Project.Kind.FOIA else 'Target end'
            events.append(_ICalEvent(
                id=f'p-target-{p.pk}@helm.docklabs.ai',
                title=f'{label_prefix}: {p.name}',
                start_time=_date_to_dt(p.target_end_at),
                end_time=_date_to_dt(p.target_end_at, end=True),
                description=p.description[:500],
                all_day=True,
            ))
        if p.completed_at:
            events.append(_ICalEvent(
                id=f'p-done-{p.pk}@helm.docklabs.ai',
                title=f'Completed: {p.name}',
                start_time=p.completed_at,
                end_time=p.completed_at,
                description='Project completed.',
                all_day=False,
            ))
        if p.kind == Project.Kind.FOIA and p.foia_statutory_deadline_at:
            jurisdiction_label = p.get_foia_jurisdiction_display()
            events.append(_ICalEvent(
                id=f'p-foia-{p.pk}@helm.docklabs.ai',
                title=f'FOIA Statutory Deadline: {p.name}',
                start_time=_date_to_dt(p.foia_statutory_deadline_at),
                end_time=_date_to_dt(p.foia_statutory_deadline_at, end=True),
                description=f'Jurisdiction: {jurisdiction_label}',
                all_day=True,
            ))

    for t in _task_visible_qs(user):
        events.append(_ICalEvent(
            id=f't-{t.pk}@helm.docklabs.ai',
            title=t.title,
            start_time=_date_to_dt(t.due_date),
            end_time=_date_to_dt(t.due_date, end=True),
            description=(t.description or '')[:500],
            all_day=True,
        ))

    ics = generate_ical(events, calendar_name='Helm Work Management')
    return ical_response(ics, filename='helm-pm.ics')
