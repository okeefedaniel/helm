"""Register Helm Tasks calendar event types with keel.calendar.

Three types — currently used only as registry entries (not pushed to
external Google/Microsoft calendars). The /tasks/calendar/ view sources
events directly from Project + Task; the iCal export at
/tasks/calendar.ics emits the same set as VEVENT entries.

A future opt-in flow could let users push their assigned tasks to Google
or Microsoft via ``keel.calendar.service.push_event()`` — that would use
these registered types as the keys.
"""
from keel.calendar import CalendarEventType, register


PROJECT_TARGET_END = 'helm.project_target_end'
PROJECT_COMPLETED = 'helm.project_completed'
TASK_DUE = 'helm.task_due'


def register_calendar_event_types():
    """Idempotent registration of Helm's calendar event types."""
    register(CalendarEventType(
        key=PROJECT_TARGET_END,
        label='Helm — Project target end',
        description='Soft deadline for project completion.',
        default_duration_minutes=15,
        include_location=False,
    ))
    register(CalendarEventType(
        key=PROJECT_COMPLETED,
        label='Helm — Project completed',
        description='Marks when a project transitioned to completed.',
        default_duration_minutes=15,
        include_location=False,
    ))
    register(CalendarEventType(
        key=TASK_DUE,
        label='Helm — Task due',
        description='A task due date.',
        default_duration_minutes=15,
        include_location=False,
    ))
