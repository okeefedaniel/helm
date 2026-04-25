"""View-layer tests for Phase 5 — claim banner, transition controls,
archive/unarchive, project list pagination, lifecycle endpoints."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from keel.accounts.models import ProductAccess

from tasks.models import (
    ArchivedProjectRecord, Project, ProjectAssignment,
    ProjectAttachment, ProjectCollaborator, ProjectNote, Task,
)
from tasks.services import (
    add_project_collaborator, archive_project, claim_project, create_project,
    transition_project,
)

User = get_user_model()


def _make_user(username='u', staff=False):
    u = User.objects.create_user(
        username=username, password='pw1234567890', email=f'{username}@t.local',
        is_staff=staff,
    )
    ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
    return u


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ProjectDetailViewTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)
        self.project = create_project(name='Detail Test', user=self.user)

    def test_claim_banner_renders_when_unclaimed(self):
        r = self.client.get(reverse('tasks:project_detail', args=[self.project.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Claim project')
        self.assertContains(r, 'no lead')

    def test_lead_indicator_renders_when_claimed(self):
        claim_project(project=self.project, user=self.user)
        r = self.client.get(reverse('tasks:project_detail', args=[self.project.slug]))
        self.assertContains(r, 'Lead:')
        # Banner should NOT show.
        self.assertNotContains(r, 'no lead')

    def test_transition_controls_render_for_lead(self):
        claim_project(project=self.project, user=self.user)
        r = self.client.get(reverse('tasks:project_detail', args=[self.project.slug]))
        # The transition form is gated by available_transitions; lead has paths.
        transition_url = reverse('tasks:project_transition', args=[self.project.slug])
        self.assertContains(r, f'action="{transition_url}"')
        # 'Pause' is the label for active → on_hold, available to a LEAD.
        self.assertContains(r, 'Pause')


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ProjectLifecycleEndpointsTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)
        self.project = create_project(name='Lifecycle Test', user=self.user)

    def test_claim_endpoint_creates_assignment(self):
        r = self.client.post(reverse('tasks:claim_project', args=[self.project.slug]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            ProjectAssignment.objects.filter(
                project=self.project, status='in_progress',
            ).count(),
            1,
        )

    def test_release_endpoint_marks_released(self):
        claim_project(project=self.project, user=self.user)
        r = self.client.post(reverse('tasks:release_project', args=[self.project.slug]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(
            ProjectAssignment.objects.filter(
                project=self.project, status='in_progress',
            ).exists(),
        )

    def test_transition_endpoint_with_valid_status(self):
        claim_project(project=self.project, user=self.user)
        r = self.client.post(
            reverse('tasks:project_transition', args=[self.project.slug]),
            {'status': 'on_hold', 'comment': 'Pausing for review'},
        )
        self.assertEqual(r.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'on_hold')

    def test_transition_endpoint_with_invalid_form(self):
        r = self.client.post(
            reverse('tasks:project_transition', args=[self.project.slug]),
            {'status': 'not-a-status'},
        )
        self.assertEqual(r.status_code, 400)

    def test_archive_endpoint_redirects_to_archive_list(self):
        claim_project(project=self.project, user=self.user)
        transition_project(
            project=self.project, user=self.user, target_status='completed',
        )
        r = self.client.post(reverse('tasks:archive_project', args=[self.project.slug]))
        self.assertEqual(r.status_code, 302)
        self.assertIn('/projects/archived/', r.url)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'archived')
        self.assertEqual(
            ArchivedProjectRecord.objects.filter(
                entity_id=str(self.project.public_id),
            ).count(),
            1,
        )

    def test_unarchive_endpoint_restores_previous_status(self):
        claim_project(project=self.project, user=self.user)
        transition_project(
            project=self.project, user=self.user, target_status='completed',
        )
        archive_project(project=self.project, user=self.user)
        r = self.client.post(reverse('tasks:unarchive_project', args=[self.project.slug]))
        self.assertEqual(r.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'completed')


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ProjectCollaboratorsViewTests(TestCase):
    def setUp(self):
        self.lead = _make_user('lead')
        self.invitee = _make_user('invitee')
        self.client.force_login(self.lead)
        self.project = create_project(name='Collab', user=self.lead)

    def test_post_invites_internal_user(self):
        r = self.client.post(
            reverse('tasks:project_collaborators', args=[self.project.slug]),
            {'user_id': self.invitee.pk, 'role': 'contributor'},
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(
            ProjectCollaborator.objects.filter(
                project=self.project, user=self.invitee, is_active=True,
            ).exists(),
        )

    def test_post_invites_external_email(self):
        r = self.client.post(
            reverse('tasks:project_collaborators', args=[self.project.slug]),
            {'email': 'ext@example.com', 'role': 'reviewer'},
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(
            ProjectCollaborator.objects.filter(
                project=self.project, email='ext@example.com', user__isnull=True,
            ).exists(),
        )

    def test_remove_endpoint_soft_deactivates(self):
        c = add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.invitee,
        )
        r = self.client.post(reverse(
            'tasks:project_collaborator_remove', args=[self.project.slug, c.pk],
        ))
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertFalse(c.is_active)


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ProjectNotesAndAttachmentsViewTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)
        self.project = create_project(name='N&A', user=self.user)

    def test_post_note_creates_note(self):
        r = self.client.post(
            reverse('tasks:project_notes', args=[self.project.slug]),
            {'content': 'Discussed RFP timeline with vendor', 'is_internal': 'on'},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            ProjectNote.objects.filter(project=self.project).count(), 1,
        )

    def test_post_attachment_creates_attachment(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('memo.txt', b'hello', content_type='text/plain')
        r = self.client.post(
            reverse('tasks:project_attachments', args=[self.project.slug]),
            {'file': f, 'description': 'Test memo', 'visibility': 'internal'},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            ProjectAttachment.objects.filter(project=self.project).count(), 1,
        )


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ArchivedProjectsViewTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.stranger = _make_user('stranger')
        self.client.force_login(self.user)
        # Two projects, only one archived, both visible to user.
        p1 = create_project(name='Alpha', user=self.user)
        p2 = create_project(name='Beta', user=self.user)
        claim_project(project=p1, user=self.user)
        transition_project(project=p1, user=self.user, target_status='completed')
        archive_project(project=p1, user=self.user)
        self.archived = p1
        self.active = p2
        # A third project the stranger created (user can't see).
        self.hidden = create_project(name='Hidden', user=self.stranger)
        claim_project(project=self.hidden, user=self.stranger)
        transition_project(project=self.hidden, user=self.stranger, target_status='completed')
        archive_project(project=self.hidden, user=self.stranger)

    def test_list_renders_archived_only(self):
        r = self.client.get(reverse('tasks:archived_projects'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Alpha')
        self.assertNotContains(r, 'Beta')  # active, not archived

    def test_list_excludes_inaccessible(self):
        r = self.client.get(reverse('tasks:archived_projects'))
        self.assertNotContains(r, 'Hidden')

    def test_list_paginates_at_25(self):
        # Create 26 archived projects accessible to user.
        for i in range(26):
            p = create_project(name=f'P{i}', user=self.user)
            claim_project(project=p, user=self.user)
            transition_project(project=p, user=self.user, target_status='completed')
            archive_project(project=p, user=self.user)
        r = self.client.get(reverse('tasks:archived_projects'))
        # Page 1 has 25 + 'Alpha' from setUp = 26 archived projects total.
        # Paginator caps at 25 per page → page 1 shows 25, page 2 shows 2.
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context['page_obj'].object_list), 25)
        self.assertTrue(r.context['page_obj'].has_next())

    def test_list_orders_newest_first(self):
        # Archive a second project AFTER setUp.archived.
        p = create_project(name='Newer', user=self.user)
        claim_project(project=p, user=self.user)
        transition_project(project=p, user=self.user, target_status='completed')
        archive_project(project=p, user=self.user)
        r = self.client.get(reverse('tasks:archived_projects'))
        names = [proj.name for proj in r.context['page_obj'].object_list]
        # Newer comes before Alpha.
        self.assertLess(names.index('Newer'), names.index('Alpha'))


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ProjectListPaginationTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)

    def test_active_list_paginates_at_25(self):
        for i in range(26):
            create_project(name=f'P{i}', user=self.user)
        r = self.client.get(reverse('tasks:project_list'))
        self.assertEqual(r.status_code, 200)
        # 26 projects → 25 on page 1.
        self.assertEqual(len(r.context['page_obj'].object_list), 25)
        self.assertTrue(r.context['page_obj'].has_next())

    def test_active_list_excludes_archived(self):
        active = create_project(name='Active One', user=self.user)
        archived = create_project(name='Archived One', user=self.user)
        claim_project(project=archived, user=self.user)
        transition_project(project=archived, user=self.user, target_status='completed')
        archive_project(project=archived, user=self.user)
        r = self.client.get(reverse('tasks:project_list'))
        self.assertContains(r, 'Active One')
        self.assertNotContains(r, 'Archived One')


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class TaskTransitionViewTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)
        self.project = create_project(name='TX', user=self.user)
        self.task = Task.objects.create(project=self.project, title='T')

    def test_task_transition_engine_validated(self):
        r = self.client.post(
            reverse('tasks:task_transition', args=[self.task.pk]),
            {'status': 'in_progress'},
        )
        self.assertEqual(r.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'in_progress')
        # TaskStatusHistory recorded by the engine.
        self.assertEqual(self.task.status_history.count(), 1)

    def test_task_transition_invalid_status_400(self):
        r = self.client.post(
            reverse('tasks:task_transition', args=[self.task.pk]),
            {'status': 'not-a-status'},
        )
        self.assertEqual(r.status_code, 400)

    def test_task_transition_blocked_path_validation(self):
        # in_progress → blocked requires a comment per TASK_WORKFLOW.
        # Without a comment, ValidationError → workflow_view → 302 (redirect)
        # for non-HTMX, or 400 for HTMX.
        self.client.post(
            reverse('tasks:task_transition', args=[self.task.pk]),
            {'status': 'in_progress'},
        )
        r = self.client.post(
            reverse('tasks:task_transition', args=[self.task.pk]),
            {'status': 'blocked'},  # no comment
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(r.status_code, 400)
