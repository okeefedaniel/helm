"""Tests for the Phase 4 per-project ACL + service-layer error mapping.

Pins the contract that:
1. ``Project.objects.visible_to(user)`` filters correctly across the four
   visibility paths (creator, active assignment, active collaborator,
   admin/staff/superuser).
2. The ``@project_access_required`` and ``@task_access_required`` decorators
   raise Http404 (NOT 403) on miss — preserves URL-space privacy per
   NIST 800-171 §3.1.1.
3. The ``@workflow_view`` decorator maps service exceptions to the right
   HTTP status with HTMX-aware payload shapes.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from tasks.access import _can_access, project_access_required, workflow_view
from tasks.models import Project, ProjectAssignment, ProjectCollaborator, Task
from tasks.services import (
    add_project_collaborator, claim_project, create_project,
)

User = get_user_model()


@override_settings(HELM_TASKS_ENABLED=True)
class VisibleToQuerySetTests(TestCase):
    """Pins the four visibility paths on ``Project.objects.visible_to()``."""

    def setUp(self):
        self.creator = User.objects.create_user(username='creator', email='c@t.local')
        self.assignee = User.objects.create_user(username='assignee', email='a@t.local')
        self.collab = User.objects.create_user(username='collab', email='co@t.local')
        self.stranger = User.objects.create_user(username='stranger', email='s@t.local')
        self.admin = User.objects.create_user(
            username='admin', email='ad@t.local', is_superuser=True, is_staff=True,
        )

        self.p_creator = create_project(name='Creator Owned', user=self.creator)
        self.p_assignee = create_project(name='Assignee Has Claim', user=self.creator)
        claim_project(project=self.p_assignee, user=self.assignee)
        self.p_collab = create_project(name='Has Active Collab', user=self.creator)
        add_project_collaborator(
            project=self.p_collab, user=self.creator, target_user=self.collab,
        )
        self.p_no_access = create_project(name='Stranger Cannot See', user=self.creator)

    def test_anonymous_user_sees_nothing(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(Project.objects.visible_to(AnonymousUser()).count(), 0)

    def test_creator_sees_their_projects(self):
        # creator has p_creator + p_assignee + p_collab + p_no_access (all created_by them)
        slugs = set(Project.objects.visible_to(self.creator).values_list('slug', flat=True))
        self.assertEqual(slugs, {
            self.p_creator.slug, self.p_assignee.slug,
            self.p_collab.slug, self.p_no_access.slug,
        })

    def test_assignee_sees_only_assigned_project(self):
        slugs = set(Project.objects.visible_to(self.assignee).values_list('slug', flat=True))
        self.assertEqual(slugs, {self.p_assignee.slug})

    def test_collaborator_sees_only_collab_project(self):
        slugs = set(Project.objects.visible_to(self.collab).values_list('slug', flat=True))
        self.assertEqual(slugs, {self.p_collab.slug})

    def test_inactive_collaborator_loses_access(self):
        coll = ProjectCollaborator.objects.get(project=self.p_collab, user=self.collab)
        coll.is_active = False
        coll.save(update_fields=['is_active'])
        slugs = set(Project.objects.visible_to(self.collab).values_list('slug', flat=True))
        self.assertEqual(slugs, set())

    def test_released_assignment_loses_access(self):
        a = ProjectAssignment.objects.get(project=self.p_assignee, assigned_to=self.assignee)
        a.status = ProjectAssignment.Status.RELEASED
        a.save(update_fields=['status'])
        slugs = set(Project.objects.visible_to(self.assignee).values_list('slug', flat=True))
        self.assertEqual(slugs, set())

    def test_stranger_sees_nothing(self):
        self.assertEqual(Project.objects.visible_to(self.stranger).count(), 0)

    def test_admin_sees_everything(self):
        # All 4 projects.
        self.assertEqual(Project.objects.visible_to(self.admin).count(), 4)

    def test_visible_to_no_duplicates_when_creator_and_collab(self):
        """A user who is BOTH creator and collaborator should still see the
        project once, not twice (distinct() guard)."""
        # creator IS the creator of p_collab AND we add them as a collaborator.
        add_project_collaborator(
            project=self.p_collab, user=self.creator, target_user=self.creator,
        )
        count = Project.objects.visible_to(self.creator).filter(
            slug=self.p_collab.slug,
        ).count()
        self.assertEqual(count, 1)


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ProjectAccessDecoratorTests(TestCase):
    """Pins that unauthorized users get 404, not 403, on slug-routed views."""

    def setUp(self):
        self.creator = User.objects.create_user(
            username='creator', password='pw1234567890', email='c@t.local',
        )
        self.stranger = User.objects.create_user(
            username='stranger', password='pw1234567890', email='s@t.local',
        )
        from keel.accounts.models import ProductAccess
        for u in (self.creator, self.stranger):
            ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
        self.project = create_project(name='Secret Project', user=self.creator)

    def test_creator_can_view_project_detail(self):
        self.client.force_login(self.creator)
        r = self.client.get(reverse('tasks:project_detail', args=[self.project.slug]))
        self.assertEqual(r.status_code, 200)

    def test_stranger_gets_404_not_403(self):
        """No info leak: 404 — identical to nonexistent slug — not 403."""
        self.client.force_login(self.stranger)
        r = self.client.get(reverse('tasks:project_detail', args=[self.project.slug]))
        self.assertEqual(r.status_code, 404)

    def test_nonexistent_slug_also_404(self):
        """Sanity — a completely fake slug behaves identically to an
        existing-but-inaccessible one. Confirms no info leak."""
        self.client.force_login(self.stranger)
        r = self.client.get(reverse('tasks:project_detail', args=['no-such-thing']))
        self.assertEqual(r.status_code, 404)

    def test_acl_blocks_task_create_on_inaccessible_project(self):
        self.client.force_login(self.stranger)
        r = self.client.get(reverse('tasks:task_create', args=[self.project.slug]))
        self.assertEqual(r.status_code, 404)

    def test_project_list_filters_to_visible(self):
        # Stranger sees no projects at all.
        self.client.force_login(self.stranger)
        r = self.client.get(reverse('tasks:project_list'))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Secret Project')


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class TaskAccessDecoratorTests(TestCase):
    """The task-scoped decorator gates by parent-project visibility."""

    def setUp(self):
        self.creator = User.objects.create_user(
            username='creator', password='pw1234567890', email='c@t.local',
        )
        self.stranger = User.objects.create_user(
            username='stranger', password='pw1234567890', email='s@t.local',
        )
        from keel.accounts.models import ProductAccess
        for u in (self.creator, self.stranger):
            ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
        self.project = create_project(name='P', user=self.creator)
        self.task = Task.objects.create(project=self.project, title='T')

    def test_stranger_gets_404_on_task_detail(self):
        self.client.force_login(self.stranger)
        r = self.client.get(reverse('tasks:task_detail', args=[self.task.pk]))
        self.assertEqual(r.status_code, 404)

    def test_stranger_blocked_on_task_status_post(self):
        self.client.force_login(self.stranger)
        r = self.client.post(
            reverse('tasks:task_status', args=[self.task.pk]),
            {'status': 'in_progress'},
        )
        self.assertEqual(r.status_code, 404)

    def test_creator_can_post_task_status(self):
        self.client.force_login(self.creator)
        r = self.client.post(
            reverse('tasks:task_status', args=[self.task.pk]),
            {'status': 'in_progress'},
        )
        self.assertEqual(r.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'in_progress')


@override_settings(HELM_TASKS_ENABLED=True)
class WorkflowViewDecoratorTests(TestCase):
    """Pins the service-error → HTTP-status mapping with HTMX awareness."""

    def setUp(self):
        self.factory = RequestFactory()

    def _wrap(self, exc):
        @workflow_view
        def view(request):
            raise exc
        return view

    def test_validation_error_maps_to_400_for_htmx(self):
        request = self.factory.post('/')
        request.headers = {'HX-Request': 'true'}
        view = self._wrap(ValidationError('bad data'))
        r = view(request)
        self.assertEqual(r.status_code, 400)
        self.assertIn(b'bad data', r.content)

    def test_validation_error_redirects_for_non_htmx(self):
        request = self.factory.post('/', HTTP_REFERER='/back/')
        # Non-HTMX: requires session middleware for messages, so just check
        # that the response is a redirect to the referer.
        from django.contrib.messages.storage.fallback import FallbackStorage
        request.session = {}
        request._messages = FallbackStorage(request)
        view = self._wrap(ValidationError('bad data'))
        r = view(request)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/back/')

    def test_permission_denied_maps_to_403_for_htmx(self):
        request = self.factory.post('/')
        request.headers = {'HX-Request': 'true'}
        view = self._wrap(PermissionDenied('not allowed'))
        r = view(request)
        self.assertEqual(r.status_code, 403)
        self.assertIn(b'not allowed', r.content)

    def test_integrity_error_maps_to_409_for_htmx(self):
        request = self.factory.post('/')
        request.headers = {'HX-Request': 'true'}
        view = self._wrap(IntegrityError('dupe'))
        r = view(request)
        self.assertEqual(r.status_code, 409)
        self.assertIn(b'conflicts with existing data', r.content)

    def test_no_exception_passes_through(self):
        request = self.factory.get('/')
        request.headers = {}

        @workflow_view
        def view(request):
            return HttpResponse('ok')
        r = view(request)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b'ok')
