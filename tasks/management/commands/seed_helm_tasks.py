"""Seed demo tasks for Helm Tasks.

Only meaningful when HELM_TASKS_ENABLED and DEMO_MODE are both true; refuses
to run otherwise so demo fixtures never leak into production DBs.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tasks.models import Project, Task, TaskComment, TaskLink


User = get_user_model()


class Command(BaseCommand):
    help = 'Seed demo projects and tasks for Helm Tasks (DEMO_MODE only).'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Seed even when DEMO_MODE is off (local dev only).')

    def handle(self, *args, **opts):
        if not getattr(settings, 'HELM_TASKS_ENABLED', False):
            raise CommandError('HELM_TASKS_ENABLED is off — nothing to seed.')
        if not opts['force'] and not getattr(settings, 'DEMO_MODE', False):
            raise CommandError('Refusing to seed without DEMO_MODE=true (use --force to override).')

        user = User.objects.order_by('pk').first()
        today = timezone.localdate()

        ops, _ = Project.objects.get_or_create(
            slug='operations',
            defaults={'name': 'Operations', 'color': 'blue', 'created_by': user,
                      'description': 'Day-to-day operations across the fleet.'},
        )
        launches, _ = Project.objects.get_or_create(
            slug='q2-launches',
            defaults={'name': 'Q2 Launches', 'color': 'teal', 'created_by': user,
                      'description': 'Product work shipping this quarter.'},
        )

        seed_rows = [
            (ops, 'Review Harbor approval backlog', 'high', Task.Status.TODO, today + timedelta(days=1)),
            (ops, 'Triage Beacon duplicate contacts', 'medium', Task.Status.IN_PROGRESS, today + timedelta(days=3)),
            (ops, 'Draft response to Admiralty FOIA request #472', 'urgent', Task.Status.TODO, today),
            (ops, 'Schedule legislator briefing (Lookout watchlist)', 'medium', Task.Status.TODO, today + timedelta(days=7)),
            (ops, 'Close the books for last fiscal period', 'high', Task.Status.BLOCKED, today - timedelta(days=1)),
            (launches, 'Ship Helm Tasks MVP', 'urgent', Task.Status.IN_PROGRESS, today + timedelta(days=5)),
            (launches, 'Brand review for new Beacon UI', 'medium', Task.Status.TODO, today + timedelta(days=10)),
            (launches, 'Migrate old Purser reports', 'low', Task.Status.DONE, today - timedelta(days=3)),
        ]
        created = 0
        for project, title, priority, status, due in seed_rows:
            if Task.objects.filter(project=project, title=title).exists():
                continue
            t = Task.objects.create(
                project=project,
                title=title,
                description=f'Demo task for {project.name}.',
                priority=priority,
                status=status,
                due_date=due,
                assignee=user,
                created_by=user,
                position=created,
            )
            if status == Task.Status.DONE:
                t.completed_at = timezone.now() - timedelta(days=3)
                t.save(update_fields=['completed_at'])
            created += 1
        # One comment + one cross-product link, to show the detail view.
        first = Task.objects.filter(project=ops).first()
        if first and user and not first.comments.exists():
            TaskComment.objects.create(task=first, author=user, body='Following up with the state agency today.')
        if first and not first.links.exists():
            TaskLink.objects.create(
                task=first, product_slug='harbor', item_type='approval',
                item_id='demo-1', url='https://demo-harbor.docklabs.ai/',
                label='Harbor — approval',
            )
        self.stdout.write(self.style.SUCCESS(f'Seeded {created} tasks across 2 projects.'))
