"""Tests for the Phase 3 project + task lifecycle services.

Pins the contract for: claim/release with Harbor reassign-on-conflict
semantics, project-scoped collaborators, transition role gating via the
``ProjectWorkflowEngine`` 'lead' resolution, archive writing both the
live row AND the retention record, and unarchive restoring the prior
terminal status.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from tasks.models import (
    ArchivedProjectRecord,
    Project,
    ProjectAssignment,
    ProjectAttachment,
    ProjectCollaborator,
    ProjectNote,
    ProjectStatusHistory,
    Task,
    TaskStatusHistory,
)
from tasks.services import (
    add_project_attachment,
    add_project_collaborator,
    add_project_note,
    archive_project,
    claim_project,
    create_project,
    create_task,
    release_project,
    remove_project_collaborator,
    transition_project,
    transition_task,
    unarchive_project,
)

User = get_user_model()


@override_settings(HELM_TASKS_ENABLED=True)
class CreateProjectTests(TestCase):
    def test_create_project_assigns_slug_and_audit(self):
        u = User.objects.create_user(username='lead1', email='l1@t.local')
        p = create_project(name='Q4 Procurement Audit', user=u)
        self.assertEqual(p.slug, 'q4-procurement-audit')
        self.assertEqual(p.status, Project.Status.ACTIVE)
        self.assertEqual(p.kind, Project.Kind.STANDARD)
        # Audit log written via keel.core.audit.log_audit (regression for
        # the 4-week silent-failure bug from before the Phase 1 hotfix).
        from core.models import AuditLog
        self.assertTrue(
            AuditLog.objects.filter(action='project.create', entity_id=str(p.pk)).exists()
        )

    def test_create_project_slug_collision(self):
        u = User.objects.create_user(username='u', email='u@t.local')
        p1 = create_project(name='Ops', user=u)
        p2 = create_project(name='Ops', user=u)
        self.assertEqual(p1.slug, 'ops')
        self.assertEqual(p2.slug, 'ops-2')


@override_settings(HELM_TASKS_ENABLED=True)
class ClaimProjectTests(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username='lead', email='lead@t.local')
        self.other = User.objects.create_user(username='other', email='other@t.local')
        self.project = create_project(name='Test Claim', user=self.lead)

    def test_claim_unowned_project_creates_assignment(self):
        a = claim_project(project=self.project, user=self.lead)
        self.assertEqual(a.assigned_to, self.lead)
        self.assertEqual(a.status, ProjectAssignment.Status.IN_PROGRESS)
        self.assertEqual(a.assignment_type, ProjectAssignment.AssignmentType.CLAIMED)

    def test_self_claim_idempotent(self):
        a1 = claim_project(project=self.project, user=self.lead)
        a2 = claim_project(project=self.project, user=self.lead)
        self.assertEqual(a1.pk, a2.pk)
        self.assertEqual(
            ProjectAssignment.objects.filter(project=self.project).count(), 1,
        )

    def test_claim_reassigns_existing(self):
        """Harbor parity — second claim closes the prior assignment as REASSIGNED."""
        a1 = claim_project(project=self.project, user=self.lead)
        a2 = claim_project(project=self.project, user=self.other)
        a1.refresh_from_db()
        self.assertEqual(a1.status, ProjectAssignment.Status.REASSIGNED)
        self.assertIsNotNone(a1.released_at)
        self.assertEqual(a2.assigned_to, self.other)
        self.assertEqual(a2.status, ProjectAssignment.Status.IN_PROGRESS)

    def test_manager_assigned_type(self):
        manager = User.objects.create_user(
            username='mgr', email='mgr@t.local', is_staff=True,
        )
        a = claim_project(project=self.project, user=self.lead, by_manager=manager)
        self.assertEqual(a.assignment_type, ProjectAssignment.AssignmentType.MANAGER_ASSIGNED)
        self.assertEqual(a.assigned_by, manager)

    def test_release_marks_assignment_released(self):
        claim_project(project=self.project, user=self.lead)
        a = release_project(project=self.project, user=self.lead, notes='handing off')
        self.assertEqual(a.status, ProjectAssignment.Status.RELEASED)
        self.assertIsNotNone(a.released_at)
        self.assertIn('handing off', a.notes)

    def test_release_when_unclaimed_returns_none(self):
        result = release_project(project=self.project, user=self.lead)
        self.assertIsNone(result)


@override_settings(HELM_TASKS_ENABLED=True)
class ProjectCollaboratorTests(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username='lead', email='lead@t.local')
        self.invitee = User.objects.create_user(username='invitee', email='inv@t.local')
        self.project = create_project(name='Collab Test', user=self.lead)

    def test_invite_internal_user_auto_accepts(self):
        c = add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.invitee,
        )
        self.assertEqual(c.user, self.invitee)
        self.assertIsNotNone(c.accepted_at)
        self.assertTrue(c.is_active)

    def test_invite_external_email_pending(self):
        c = add_project_collaborator(
            project=self.project, user=self.lead, email='external@example.com',
        )
        self.assertIsNone(c.user)
        self.assertEqual(c.email, 'external@example.com')
        self.assertIsNone(c.accepted_at)

    def test_invite_raises_without_target_or_email(self):
        with self.assertRaises(ValueError):
            add_project_collaborator(project=self.project, user=self.lead)

    def test_remove_soft_deactivates(self):
        c = add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.invitee,
        )
        remove_project_collaborator(collaborator=c, user=self.lead)
        c.refresh_from_db()
        self.assertFalse(c.is_active)


@override_settings(HELM_TASKS_ENABLED=True)
class ProjectNoteAndAttachmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', email='u@t.local')
        self.project = create_project(name='Notes & Files', user=self.user)

    def test_add_project_note(self):
        n = add_project_note(
            project=self.project, user=self.user, content='Diligence note',
        )
        self.assertEqual(n.author, self.user)
        self.assertEqual(n.content, 'Diligence note')
        self.assertTrue(n.is_internal)
        self.assertEqual(self.project.notes.count(), 1)

    def test_add_project_attachment(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('memo.txt', b'hello', content_type='text/plain')
        a = add_project_attachment(
            project=self.project, user=self.user, file=f, description='Test memo',
        )
        self.assertEqual(a.uploaded_by, self.user)
        self.assertEqual(a.filename, 'memo.txt')
        self.assertEqual(a.size_bytes, 5)
        self.assertEqual(a.visibility, ProjectAttachment.Visibility.INTERNAL)


@override_settings(HELM_TASKS_ENABLED=True)
class TransitionTests(TestCase):
    """Pins the ProjectWorkflowEngine 'lead' role resolution and the
    completed_at side-effect on transition_project / transition_task.
    """

    def setUp(self):
        self.lead = User.objects.create_user(username='lead', email='lead@t.local')
        self.stranger = User.objects.create_user(username='strn', email='s@t.local')
        self.admin = User.objects.create_user(
            username='adm', email='a@t.local', is_superuser=True, is_staff=True,
        )
        self.project = create_project(name='WF Test', user=self.lead)
        claim_project(project=self.project, user=self.lead)

    def test_lead_can_transition_owned_project(self):
        """Regression for the 'lead' role bug — keel base couldn't resolve
        this; ProjectWorkflowEngine subclass does."""
        transition_project(
            project=self.project, user=self.lead,
            target_status=Project.Status.ON_HOLD,
            comment='Pausing for budget review',
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.ON_HOLD)
        # Status history auto-recorded by the engine.
        self.assertEqual(self.project.status_history.count(), 1)

    def test_stranger_cannot_transition(self):
        with self.assertRaises(PermissionDenied):
            transition_project(
                project=self.project, user=self.stranger,
                target_status=Project.Status.ON_HOLD,
                comment='Sneaky pause',
            )

    def test_lead_collaborator_can_transition(self):
        """A user with role=LEAD on the project's collaborator set is also
        recognized as 'lead', not just the active assignee."""
        co_lead = User.objects.create_user(username='colead', email='c@t.local')
        add_project_collaborator(
            project=self.project, user=self.lead, target_user=co_lead,
            role=ProjectCollaborator.Role.LEAD,
        )
        transition_project(
            project=self.project, user=co_lead,
            target_status=Project.Status.ON_HOLD,
            comment='Co-lead pause',
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.ON_HOLD)

    def test_system_admin_can_transition(self):
        transition_project(
            project=self.project, user=self.admin,
            target_status=Project.Status.ON_HOLD,
            comment='Admin pause',
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.ON_HOLD)

    def test_completed_stamps_completed_at(self):
        transition_project(
            project=self.project, user=self.lead,
            target_status=Project.Status.COMPLETED,
        )
        self.project.refresh_from_db()
        self.assertIsNotNone(self.project.completed_at)

    def test_invalid_transition_raises_validation(self):
        # active → archived requires going through completed first.
        with self.assertRaises(ValidationError):
            transition_project(
                project=self.project, user=self.lead,
                target_status=Project.Status.ARCHIVED,
            )

    def test_transition_task_done_stamps_completed_at(self):
        task = create_task(project=self.project, title='T', user=self.lead)
        transition_task(
            task=task, user=self.lead, target_status=Task.Status.IN_PROGRESS,
        )
        transition_task(
            task=task, user=self.lead, target_status=Task.Status.DONE,
        )
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.DONE)
        self.assertIsNotNone(task.completed_at)
        # Status history written for both transitions.
        self.assertEqual(task.status_history.count(), 2)

    def test_transition_task_reopen_clears_completed_at(self):
        task = create_task(project=self.project, title='T', user=self.lead)
        transition_task(task=task, user=self.lead, target_status=Task.Status.DONE)
        transition_task(
            task=task, user=self.lead, target_status=Task.Status.IN_PROGRESS,
        )
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)


@override_settings(HELM_TASKS_ENABLED=True)
class ArchiveTests(TestCase):
    """Archive writes the live archived_at AND a retention record. Unarchive
    restores previous_terminal_status."""

    def setUp(self):
        self.lead = User.objects.create_user(username='lead', email='lead@t.local')
        self.project = create_project(name='Archive Test', user=self.lead)
        claim_project(project=self.project, user=self.lead)
        # Drive to a terminal state so we can archive.
        transition_project(
            project=self.project, user=self.lead,
            target_status=Project.Status.COMPLETED,
        )

    def test_archive_writes_both_rows(self):
        archive_project(project=self.project, user=self.lead, comment='Done')
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.ARCHIVED)
        self.assertIsNotNone(self.project.archived_at)
        self.assertEqual(self.project.previous_terminal_status, Project.Status.COMPLETED)
        # Retention row exists with the correct policy.
        rec = ArchivedProjectRecord.objects.get(entity_id=str(self.project.public_id))
        self.assertEqual(
            rec.retention_policy, ArchivedProjectRecord.RetentionPolicy.STANDARD,
        )
        self.assertIsNotNone(rec.retention_expires_at)
        self.assertEqual(rec.metadata['slug'], self.project.slug)

    def test_archive_permanent_retention_no_expiry(self):
        archive_project(
            project=self.project, user=self.lead,
            retention=ArchivedProjectRecord.RetentionPolicy.PERMANENT,
        )
        rec = ArchivedProjectRecord.objects.get(entity_id=str(self.project.public_id))
        self.assertEqual(
            rec.retention_policy, ArchivedProjectRecord.RetentionPolicy.PERMANENT,
        )
        self.assertIsNone(rec.retention_expires_at)

    def test_archive_idempotent(self):
        archive_project(project=self.project, user=self.lead)
        first_archived_at = self.project.archived_at
        archive_project(project=self.project, user=self.lead)
        # No second retention row; first archived_at preserved.
        self.assertEqual(
            ArchivedProjectRecord.objects.filter(
                entity_id=str(self.project.public_id),
            ).count(),
            1,
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.archived_at, first_archived_at)

    def test_unarchive_restores_previous_terminal_status(self):
        archive_project(project=self.project, user=self.lead)
        unarchive_project(project=self.project, user=self.lead)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.COMPLETED)
        self.assertIsNone(self.project.archived_at)
        self.assertEqual(self.project.previous_terminal_status, '')

    def test_unarchive_no_op_on_active_project(self):
        active = create_project(name='Still Active', user=self.lead)
        unarchive_project(project=active, user=self.lead)
        active.refresh_from_db()
        self.assertEqual(active.status, Project.Status.ACTIVE)
