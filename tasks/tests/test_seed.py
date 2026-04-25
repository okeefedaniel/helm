"""Phase 10 demo seed tests.

Pins:
- The seed creates exactly four projects with the expected final statuses.
- Idempotent — re-running is a no-op.
- Refuses to run without DEMO_MODE (production safety).
- The archived demo project actually has archived_at set + a retention row.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from tasks.models import (
    ArchivedProjectRecord, Project, ProjectAssignment, Task,
)

User = get_user_model()


@override_settings(HELM_TASKS_ENABLED=True, DEMO_MODE=True)
class SeedDemoProjectsTests(TestCase):
    def setUp(self):
        # Seed needs at least one user. Create a superuser as the lead.
        self.lead = User.objects.create_user(
            username='dokadmin', email='dok@dok.net',
            is_superuser=True, is_staff=True,
        )
        # Create a second user so the collaborator branch fires.
        self.collab = User.objects.create_user(
            username='analyst', email='analyst@docklabs.ai',
        )

    def test_seed_creates_four_projects(self):
        call_command('seed_demo_projects')
        self.assertEqual(Project.objects.count(), 4)
        slugs = set(Project.objects.values_list('slug', flat=True))
        self.assertEqual(slugs, {
            'q3-grant-portfolio',
            'arpa-spring-rfp-foia',
            'capital-improvement-2025',
            'archived-pilot-2024',
        })

    def test_seed_assigns_correct_final_statuses(self):
        call_command('seed_demo_projects')
        statuses = dict(Project.objects.values_list('slug', 'status'))
        self.assertEqual(statuses['q3-grant-portfolio'], 'active')
        self.assertEqual(statuses['arpa-spring-rfp-foia'], 'active')
        self.assertEqual(statuses['capital-improvement-2025'], 'completed')
        self.assertEqual(statuses['archived-pilot-2024'], 'archived')

    def test_seed_creates_foia_project_with_metadata(self):
        call_command('seed_demo_projects')
        foia = Project.objects.get(slug='arpa-spring-rfp-foia')
        self.assertEqual(foia.kind, Project.Kind.FOIA)
        self.assertEqual(foia.foia_metadata['foia_request_id'], 'FOIA-2026-0421')
        self.assertEqual(foia.foia_metadata['requester_organization'], 'Hartford Courant')

    def test_seed_archived_project_writes_retention_row(self):
        call_command('seed_demo_projects')
        archived = Project.objects.get(slug='archived-pilot-2024')
        self.assertIsNotNone(archived.archived_at)
        self.assertEqual(archived.previous_terminal_status, 'completed')
        self.assertTrue(
            ArchivedProjectRecord.objects.filter(
                entity_id=str(archived.public_id),
            ).exists(),
        )

    def test_seed_claims_each_project(self):
        call_command('seed_demo_projects')
        # Every project (except archived) should have an active assignment.
        # Archived projects have IN_PROGRESS assignments too — claim happens
        # before the archive transition.
        for p in Project.objects.all():
            self.assertTrue(
                ProjectAssignment.objects.filter(
                    project=p, assigned_to=self.lead,
                ).exists(),
                msg=f'No assignment for {p.slug}',
            )

    def test_seed_creates_tasks_per_project(self):
        call_command('seed_demo_projects')
        counts = {p.slug: p.tasks.count() for p in Project.objects.all()}
        self.assertEqual(counts['q3-grant-portfolio'], 4)
        self.assertEqual(counts['arpa-spring-rfp-foia'], 3)
        self.assertEqual(counts['capital-improvement-2025'], 2)
        self.assertEqual(counts['archived-pilot-2024'], 1)

    def test_seed_drives_tasks_through_workflow(self):
        call_command('seed_demo_projects')
        # The capital improvement project's tasks should all be DONE,
        # which means transition_task ran and TaskStatusHistory rows exist.
        cip = Project.objects.get(slug='capital-improvement-2025')
        for t in cip.tasks.all():
            self.assertEqual(t.status, Task.Status.DONE)
            self.assertIsNotNone(t.completed_at)
            # Engine-recorded history.
            self.assertGreaterEqual(t.status_history.count(), 1)

    def test_seed_is_idempotent(self):
        call_command('seed_demo_projects')
        first_count = Project.objects.count()
        # Re-run — should be a no-op (no extra projects created).
        call_command('seed_demo_projects')
        self.assertEqual(Project.objects.count(), first_count)


@override_settings(HELM_TASKS_ENABLED=True, DEMO_MODE=False)
class SeedDemoProjectsSafetyTests(TestCase):
    def test_refuses_without_demo_mode(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('seed_demo_projects')
        self.assertIn('DEMO_MODE', str(ctx.exception))

    def test_force_flag_bypasses_demo_mode_check(self):
        User.objects.create_user(
            username='admin', email='admin@docklabs.ai',
            is_superuser=True, is_staff=True,
        )
        call_command('seed_demo_projects', '--force')
        self.assertEqual(Project.objects.count(), 4)


@override_settings(HELM_TASKS_ENABLED=False)
class SeedDemoProjectsDisabledTests(TestCase):
    def test_refuses_when_helm_tasks_disabled(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('seed_demo_projects', '--force')
        self.assertIn('HELM_TASKS_ENABLED', str(ctx.exception))
