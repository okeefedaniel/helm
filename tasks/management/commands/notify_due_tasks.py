"""Daily cron — fire helm_task_due_soon and helm_task_overdue notifications.

Runs once daily (typically 09:00 UTC). Two passes:

1. **Due-soon** — tasks whose ``due_date`` falls within the next 12-36
   hours. Idempotent via ``Task.last_due_soon_notif_at`` (one notification
   per task per day).

2. **Overdue** — tasks past their ``due_date`` and not yet DONE. Idempotent
   via ``Task.last_overdue_notif_at`` with a 24h cooldown so the same task
   doesn't spam its assignee daily forever.

Registered with ``keel.scheduling`` so the dashboard at ``/scheduling/``
shows last-run state, recent run history, and any errors.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone

from keel.notifications import notify
from keel.scheduling import scheduled_job

from tasks.models import Task


@scheduled_job(
    slug='helm-notify-due-tasks',
    name='Helm — Daily due-task notifications',
    cron='0 9 * * *',
    owner='helm',
    description=(
        'Fires helm_task_due_soon for tasks due in the next 12-36h, '
        'and helm_task_overdue for tasks past their due date. Idempotent '
        'via Task.last_due_soon_notif_at and Task.last_overdue_notif_at.'
    ),
    notes='Scheduled at 09:00 UTC daily.',
    timeout_minutes=10,
)
class Command(BaseCommand):
    help = 'Fire helm_task_due_soon and helm_task_overdue notifications.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would fire without dispatching.',
        )

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        now = timezone.now()
        # Use localdate so "today" matches the user's calendar day, not UTC.
        # After ~8pm EDT, ``now.date()`` rolls into UTC tomorrow and same-day
        # tasks fall into the overdue bucket instead of due-soon.
        today = timezone.localdate()

        # --- Due-soon: tasks whose due_date is in 12-36h, no notif today ---
        soon_window_start = today + timedelta(days=0)  # earliest tomorrow at midnight
        soon_window_end = today + timedelta(days=2)
        due_soon_qs = Task.objects.filter(
            due_date__gte=today,
            due_date__lte=soon_window_end,
            status__in=[Task.Status.TODO, Task.Status.IN_PROGRESS, Task.Status.BLOCKED],
            assignee__isnull=False,
        ).exclude(
            # Already notified within the last 23h.
            last_due_soon_notif_at__gte=now - timedelta(hours=23),
        ).select_related('project', 'assignee')

        soon_count = 0
        for task in due_soon_qs:
            if dry:
                self.stdout.write(
                    f'  would fire helm_task_due_soon → {task.assignee.email}: {task.title}'
                )
            else:
                notify(
                    event='helm_task_due_soon',
                    context={'task': task, 'project': task.project, 'title': task.title},
                )
                task.last_due_soon_notif_at = now
                task.save(update_fields=['last_due_soon_notif_at'])
            soon_count += 1

        # --- Overdue: due_date < today, not done, 24h cooldown ---
        overdue_qs = Task.objects.filter(
            due_date__lt=today,
            status__in=[Task.Status.TODO, Task.Status.IN_PROGRESS, Task.Status.BLOCKED],
            assignee__isnull=False,
        ).filter(
            models.Q(last_overdue_notif_at__isnull=True)
            | models.Q(last_overdue_notif_at__lt=now - timedelta(hours=23)),
        ).select_related('project', 'assignee')

        overdue_count = 0
        for task in overdue_qs:
            if dry:
                self.stdout.write(
                    f'  would fire helm_task_overdue → {task.assignee.email}: {task.title}'
                )
            else:
                notify(
                    event='helm_task_overdue',
                    context={'task': task, 'project': task.project, 'title': task.title},
                )
                task.last_overdue_notif_at = now
                task.save(update_fields=['last_overdue_notif_at'])
            overdue_count += 1

        verb = 'Would fire' if dry else 'Fired'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {soon_count} due-soon and {overdue_count} overdue notifications.'
        ))
