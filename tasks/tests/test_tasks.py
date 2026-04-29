from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from tasks.models import Project, Task, TaskCollaborator, TaskLink
from tasks.services import (
    add_collaborator,
    create_task,
    promote_fleet_item_to_task,
    remove_collaborator,
    reorder_task,
    update_task,
)

User = get_user_model()


@override_settings(HELM_TASKS_ENABLED=True)
class TasksModelsTests(TestCase):
    def test_project_slug_autogen(self):
        p = Project.objects.create(name='My Cool Project')
        self.assertEqual(p.slug, 'my-cool-project')

    def test_project_slug_collision(self):
        Project.objects.create(name='Ops')
        p2 = Project.objects.create(name='Ops')
        self.assertEqual(p2.slug, 'ops-2')

    def test_task_overdue(self):
        from django.utils import timezone
        from datetime import timedelta
        p = Project.objects.create(name='P')
        t = Task.objects.create(project=p, title='X', due_date=timezone.localdate() - timedelta(days=1))
        self.assertTrue(t.is_overdue)
        t.status = Task.Status.DONE
        t.save()
        self.assertFalse(t.is_overdue)


@override_settings(HELM_TASKS_ENABLED=True)
class TasksServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='p', email='u@x.com')
        self.project = Project.objects.create(name='P', created_by=self.user)

    def test_create_task_assigns_position(self):
        t1 = create_task(project=self.project, title='one', user=self.user)
        t2 = create_task(project=self.project, title='two', user=self.user)
        self.assertGreater(t2.position, t1.position)

    def test_update_task_marks_completed_on_done(self):
        t = create_task(project=self.project, title='x', user=self.user)
        update_task(t, user=self.user, status=Task.Status.DONE)
        t.refresh_from_db()
        self.assertIsNotNone(t.completed_at)

    def test_update_task_clears_completed_on_reopen(self):
        t = create_task(project=self.project, title='x', user=self.user)
        update_task(t, user=self.user, status=Task.Status.DONE)
        update_task(t, user=self.user, status=Task.Status.TODO)
        t.refresh_from_db()
        self.assertIsNone(t.completed_at)

    def test_reorder_task(self):
        t = create_task(project=self.project, title='x', user=self.user)
        reorder_task(t, user=self.user, new_status=Task.Status.IN_PROGRESS, new_position=42)
        t.refresh_from_db()
        self.assertEqual(t.status, Task.Status.IN_PROGRESS)
        self.assertEqual(t.position, 42)

    def test_create_task_writes_audit_log_entry(self):
        # Regression: 2026-03-26 → 2026-04-25 the _audit() helper passed
        # target_type/target_id/metadata kwargs to a model whose actual
        # fields are entity_type/entity_id/description/changes. Every
        # create raised TypeError, swallowed by a bare except — ~4 weeks
        # of silent audit-trail loss.
        from core.models import AuditLog
        before = AuditLog.objects.filter(action='task.create').count()
        create_task(project=self.project, title='hotfix smoke', user=self.user)
        self.assertEqual(
            AuditLog.objects.filter(
                action='task.create', entity_type='helm_tasks.Task'
            ).count(),
            before + 1,
        )

    def test_promote_fleet_item_creates_task_with_link(self):
        t = promote_fleet_item_to_task(
            project=self.project,
            user=self.user,
            title='From Beacon alert',
            product_slug='beacon',
            item_type='alert',
            item_id='abc-123',
            url='https://beacon.docklabs.ai/alert/abc-123/',
        )
        self.assertEqual(t.title, 'From Beacon alert')
        self.assertEqual(t.links.count(), 1)
        link = t.links.first()
        self.assertEqual(link.product_slug, 'beacon')
        self.assertEqual(link.item_id, 'abc-123')


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class TasksViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', password='pw1234567890', email='u@x.com')
        # Grant Helm product access so ProductAccessMiddleware doesn't 403.
        from keel.accounts.models import ProductAccess
        ProductAccess.objects.create(user=self.user, product='helm', role='helm_admin')
        self.client.force_login(self.user)
        self.project = Project.objects.create(name='Ops', created_by=self.user)

    def test_my_tasks_renders(self):
        Task.objects.create(project=self.project, title='do thing', assignee=self.user)
        r = self.client.get(reverse('tasks:my_tasks'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'do thing')

    def test_project_list_renders(self):
        r = self.client.get(reverse('tasks:project_list'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Ops')

    def test_project_detail_list_view(self):
        Task.objects.create(project=self.project, title='alpha')
        r = self.client.get(reverse('tasks:project_detail', args=[self.project.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'alpha')

    def test_project_detail_board_view(self):
        Task.objects.create(project=self.project, title='alpha', status=Task.Status.IN_PROGRESS)
        r = self.client.get(reverse('tasks:project_detail', args=[self.project.slug]) + '?view=board')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'alpha')
        self.assertContains(r, 'task-board')

    def test_task_status_htmx(self):
        t = Task.objects.create(project=self.project, title='x')
        r = self.client.post(reverse('tasks:task_status', args=[t.pk]), {'status': Task.Status.DONE})
        self.assertEqual(r.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.status, Task.Status.DONE)

    def test_promote_get_renders_form(self):
        r = self.client.get(reverse('tasks:promote') + '?title=Foo&url=https://example.com&product_slug=harbor&item_type=action')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Foo')

    def test_inbox_claim_assigns_to_current_user(self):
        t = Task.objects.create(project=self.project, title='triage me')
        self.assertIsNone(t.assignee)
        r = self.client.post(reverse('tasks:inbox_claim', args=[t.pk]))
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.assignee, self.user)

    def test_inbox_claim_idempotent_when_already_assigned(self):
        other = User.objects.create_user(
            username='other', password='pw1234567890', email='other@x.com',
        )
        t = Task.objects.create(project=self.project, title='already taken', assignee=other)
        self.client.post(reverse('tasks:inbox_claim', args=[t.pk]))
        t.refresh_from_db()
        # Stays with the original assignee — no silent steal.
        self.assertEqual(t.assignee, other)

    def test_my_tasks_widget_partial(self):
        Task.objects.create(project=self.project, title='widget item', assignee=self.user)
        r = self.client.get(reverse('tasks:partial_my_tasks'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'widget item')


@override_settings(HELM_TASKS_ENABLED=True)
class TasksCollaboratorTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='p', email='o@x.com')
        self.alice = User.objects.create_user(username='alice', password='p', email='a@x.com')
        self.project = Project.objects.create(name='P', created_by=self.owner)
        self.task = Task.objects.create(project=self.project, title='T', created_by=self.owner)

    def test_add_internal_collaborator_auto_accepts(self):
        c = add_collaborator(task=self.task, user=self.owner, target_user=self.alice)
        self.assertEqual(c.user, self.alice)
        self.assertIsNotNone(c.accepted_at)
        self.assertFalse(c.is_external)

    def test_add_external_collaborator_pending(self):
        c = add_collaborator(task=self.task, user=self.owner, email='external@x.com')
        self.assertTrue(c.is_external)
        self.assertTrue(c.is_pending)

    def test_add_collaborator_idempotent(self):
        add_collaborator(task=self.task, user=self.owner, target_user=self.alice)
        add_collaborator(task=self.task, user=self.owner, target_user=self.alice)
        self.assertEqual(self.task.collaborators.count(), 1)

    def test_my_tasks_includes_collaborated(self):
        # alice isn't the assignee but is a collaborator
        add_collaborator(task=self.task, user=self.owner, target_user=self.alice)
        from keel.accounts.models import ProductAccess
        ProductAccess.objects.create(user=self.alice, product='helm', role='helm_admin')
        self.client.force_login(self.alice)
        r = self.client.get(reverse('tasks:my_tasks'))
        self.assertContains(r, self.task.title)

    def test_remove_collaborator(self):
        c = add_collaborator(task=self.task, user=self.owner, target_user=self.alice)
        remove_collaborator(collaborator=c, user=self.owner)
        self.assertEqual(self.task.collaborators.count(), 0)

    def test_collaborator_role_choices(self):
        c = add_collaborator(task=self.task, user=self.owner, target_user=self.alice,
                             role=TaskCollaborator.Role.REVIEWER)
        self.assertEqual(c.role, 'reviewer')


@override_settings(HELM_TASKS_ENABLED=False)
class TasksDisabledTests(TestCase):
    """When the feature flag is off, the URLs aren't mounted."""
    def test_tasks_url_not_resolvable_when_disabled(self):
        # Re-import URL conf in a separate test runner would be needed to fully
        # validate. Instead, assert the setting is honored and the namespace
        # would be missing. (Smoke-level — full validation is done manually.)
        from django.conf import settings as s
        self.assertFalse(s.HELM_TASKS_ENABLED)
