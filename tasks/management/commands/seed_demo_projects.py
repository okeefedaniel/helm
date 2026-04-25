"""Seed demo projects exercising the full Phase 3 lifecycle.

Creates four projects covering every state of the Project workflow:

    q3-grant-portfolio          standard,  active     (claimed, with collab)
    arpa-spring-rfp-foia        foia,      active     (claimed, with collab + foia_metadata)
    capital-improvement-2025    standard,  completed  (done tasks, completed_at stamped)
    archived-pilot-2024         standard,  archived   (driven through completed → archived)

Useful for:
- Screenshotting the PM UI with realistic data
- Dogfooding /qa flows against varied project states
- Demonstrating the FOIA project type (Phase 9 will hang real Admiralty
  metadata off this project's foia_metadata field)
- Showing the archived list at /tasks/projects/archived/

Idempotent: if any of the four slugs already exist, the seed is a no-op.
This means you can safely re-run after a deploy without risk of duplicate
projects, tasks, collaborators, or notifications.

Refuses to run unless DEMO_MODE=true (or --force is passed) so demo
fixtures never leak into production. HELM_TASKS_ENABLED must also be true.

Wire into startup.py under DEMO_MODE so demo deploys auto-seed on boot:

    if os.getenv('DEMO_MODE', '').lower() == 'true':
        run('python manage.py seed_demo_projects', fatal=False)
"""
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tasks.models import Project, Task
from tasks.services import (
    add_project_collaborator, add_project_note, archive_project,
    claim_project, create_project, create_task, transition_project,
    transition_task,
)


User = get_user_model()


# Each project: slug, name, kind, color, description, target_status,
# foia_metadata (optional), tasks (title, priority, status), notes.
PROJECTS = [
    {
        'slug': 'q3-grant-portfolio',
        'name': 'Q3 Federal Grant Portfolio',
        'kind': Project.Kind.STANDARD,
        'color': 'blue',
        'description': 'Quarterly review of federal grant pipeline + sub-recipient performance.',
        'target_status': Project.Status.ACTIVE,
        'tasks': [
            ('Inventory active grant programs', Task.Priority.HIGH, Task.Status.IN_PROGRESS, 5),
            ('Audit FY26 drawdown schedule', Task.Priority.MEDIUM, Task.Status.TODO, 12),
            ('Compile sub-recipient performance reports', Task.Priority.URGENT, Task.Status.BLOCKED, 3),
            ('Schedule program manager check-ins', Task.Priority.LOW, Task.Status.TODO, 21),
        ],
        'notes': ['Kicked off Q3 review; comptroller wants weekly status updates.'],
    },
    {
        'slug': 'arpa-spring-rfp-foia',
        'name': 'ARPA Spring RFP FOIA',
        'kind': Project.Kind.FOIA,
        'color': 'orange',
        'description': 'Records request from Hartford Courant — ARPA spring RFP responses.',
        'target_status': Project.Status.ACTIVE,
        'foia_metadata': {
            'foia_request_id': 'FOIA-2026-0421',
            'foia_agency': 'State Comptroller',
            'requester_organization': 'Hartford Courant',
            'requester_name': 'Jane Doe',
        },
        # ADD-2 — populate the statutory clock so the demo countdown badge
        # is meaningful. Received 7 calendar days ago; deadline computed
        # by recompute_deadline().
        'foia_received_offset_days': -7,
        'tasks': [
            ('Search responsive records', Task.Priority.HIGH, Task.Status.IN_PROGRESS, 4),
            ('Review for exemptions (CGS §1-210(b))', Task.Priority.HIGH, Task.Status.TODO, 8),
            ('Redact and prepare release packet', Task.Priority.MEDIUM, Task.Status.TODO, 14),
        ],
        'notes': ['Counsel reviewing scope. Statutory deadline auto-computed from received_at.'],
    },
    {
        'slug': 'capital-improvement-2025',
        'name': 'Capital Improvement Plan 2025',
        'kind': Project.Kind.STANDARD,
        'color': 'green',
        'description': 'CIP rollout — closeout phase.',
        'target_status': Project.Status.COMPLETED,
        'tasks': [
            ('Final reconciliation with treasurer', Task.Priority.HIGH, Task.Status.DONE, -7),
            ('Council presentation', Task.Priority.HIGH, Task.Status.DONE, -3),
        ],
        'notes': ['Closeout signed off by council on April 18.'],
    },
    {
        'slug': 'archived-pilot-2024',
        'name': 'Pilot Program 2024 (Archived)',
        'kind': Project.Kind.STANDARD,
        'color': 'gray',
        'description': 'Pilot wrapped Q4 2024, archived for retention.',
        'target_status': Project.Status.ARCHIVED,
        'tasks': [
            ('Closeout report', Task.Priority.MEDIUM, Task.Status.DONE, -90),
        ],
        'notes': [],
    },
]


class Command(BaseCommand):
    help = 'Seed four demo projects exercising the full PM lifecycle.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Seed even when DEMO_MODE is off (local dev only).',
        )

    def handle(self, *args, **opts):
        if not getattr(settings, 'HELM_TASKS_ENABLED', False):
            raise CommandError('HELM_TASKS_ENABLED is off — nothing to seed.')
        if not opts['force'] and not getattr(settings, 'DEMO_MODE', False):
            raise CommandError(
                'Refusing to seed without DEMO_MODE=true (use --force for local dev).'
            )

        # Idempotency: if any of our four slugs already exist, bail.
        existing = list(
            Project.objects.filter(slug__in=[p['slug'] for p in PROJECTS])
                           .values_list('slug', flat=True)
        )
        if existing:
            self.stdout.write(self.style.WARNING(
                f'Demo projects already exist ({", ".join(existing)}); skipping seed.'
            ))
            return

        # Lead user — use the first superuser. Fall back to the first user.
        lead = (
            User.objects.filter(is_superuser=True).order_by('pk').first()
            or User.objects.order_by('pk').first()
        )
        if lead is None:
            raise CommandError(
                'No users in DB to seed against. Run seed_keel_users first.'
            )

        # Optional secondary collaborator — second user if one exists.
        collab = (User.objects.exclude(pk=lead.pk).order_by('pk').first())

        today = timezone.localdate()

        for spec in PROJECTS:
            project = self._seed_project(spec, lead=lead, collab=collab, today=today)
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ {project.slug} → {project.status}'
            ))

        self.stdout.write(self.style.SUCCESS(
            f'\nSeeded {len(PROJECTS)} demo projects.'
        ))

    def _seed_project(self, spec, *, lead, collab, today):
        project = create_project(
            name=spec['name'], user=lead,
            description=spec['description'],
            color=spec['color'], kind=spec['kind'],
            started_at=today - timedelta(days=30),
            target_end_at=today + timedelta(days=60),
        )
        # The slug auto-generated from name may differ from our intended
        # slug; align them so URL paths match plan expectations.
        if project.slug != spec['slug']:
            project.slug = spec['slug']
            project.save(update_fields=['slug'])

        # FOIA metadata + statutory clock.
        if spec.get('foia_metadata'):
            project.foia_metadata = spec['foia_metadata']
            project.save(update_fields=['foia_metadata'])
        if spec.get('foia_received_offset_days') is not None:
            from tasks.foia import recompute_deadline
            project.foia_received_at = today + timedelta(
                days=spec['foia_received_offset_days'],
            )
            project.foia_jurisdiction = Project.FOIAJurisdiction.FEDERAL
            project.save(update_fields=['foia_received_at', 'foia_jurisdiction'])
            recompute_deadline(project)

        # Claim. Use manager-initiated path so the lead gets a notification
        # in their in-app feed when the seed runs (only matters for demo
        # screenshots — has no effect on the data shape).
        claim_project(project=project, user=lead)

        # Add a collaborator if we have a second user.
        if collab is not None:
            add_project_collaborator(
                project=project, user=lead, target_user=collab,
            )

        # Tasks.
        for title, priority, status, due_days in spec['tasks']:
            task = create_task(
                project=project, title=title, user=lead,
                priority=priority, status=Task.Status.TODO,
                due_date=today + timedelta(days=due_days),
            )
            # Drive the task through the workflow if the spec wants it past TODO.
            if status == Task.Status.IN_PROGRESS:
                transition_task(task=task, user=lead, target_status='in_progress')
            elif status == Task.Status.BLOCKED:
                transition_task(task=task, user=lead, target_status='in_progress')
                transition_task(
                    task=task, user=lead, target_status='blocked',
                    comment='Awaiting external dependency.',
                )
            elif status == Task.Status.DONE:
                transition_task(task=task, user=lead, target_status='in_progress')
                transition_task(task=task, user=lead, target_status='done')

        # Notes.
        for body in spec.get('notes', []):
            add_project_note(project=project, user=lead, content=body)

        # Drive project to its target status. transition_project enforces
        # the engine, so we use intermediate steps when needed.
        target = spec['target_status']
        if target == Project.Status.COMPLETED:
            transition_project(project=project, user=lead, target_status='completed')
        elif target == Project.Status.ARCHIVED:
            transition_project(project=project, user=lead, target_status='completed')
            archive_project(project=project, user=lead, comment='Demo seed: archived.')

        return project
