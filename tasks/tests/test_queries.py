"""Tests for tasks.queries — the dashboard's deadline-rail data source."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.models import Task
from tasks.queries import (
    get_user_deadline_rail,
    get_user_open_task_count,
    get_user_tasks_by_project,
    get_user_undated_count,
)
from tasks.services import (
    add_project_collaborator, create_project, create_task,
)

User = get_user_model()


@override_settings(HELM_TASKS_ENABLED=True)
class DeadlineRailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dok', email='dok@t.local')
        self.other = User.objects.create_user(username='other', email='o@t.local')
        self.project = create_project(name='Test Project', user=self.user)
        self.today = timezone.localdate()

    def _task(self, title, due_date=None, assignee=None, status=Task.Status.TODO):
        t = create_task(
            project=self.project,
            user=self.user,
            title=title,
            due_date=due_date,
            assignee=assignee or self.user,
        )
        if status != t.status:
            t.status = status
            t.save(update_fields=['status'])
        return t

    def test_overdue_today_thisweek_upcoming_buckets(self):
        self._task('overdue', self.today - timedelta(days=3))
        self._task('today', self.today)
        self._task('this_week', self.today + timedelta(days=4))
        self._task('upcoming', self.today + timedelta(days=10))
        self._task('out_of_horizon', self.today + timedelta(days=60))

        rail = get_user_deadline_rail(self.user, weeks_ahead=2)
        self.assertEqual([t.title for t in rail['overdue']], ['overdue'])
        self.assertEqual([t.title for t in rail['today']], ['today'])
        self.assertEqual([t.title for t in rail['this_week']], ['this_week'])
        self.assertEqual([t.title for t in rail['upcoming']], ['upcoming'])

    def test_done_tasks_excluded(self):
        self._task('done overdue', self.today - timedelta(days=2),
                   status=Task.Status.DONE)
        rail = get_user_deadline_rail(self.user)
        self.assertEqual(rail['overdue'], [])

    def test_other_users_tasks_excluded(self):
        other_project = create_project(name='Other Project', user=self.other)
        create_task(
            project=other_project, user=self.other,
            title='not mine', due_date=self.today, assignee=self.other,
        )
        rail = get_user_deadline_rail(self.user)
        self.assertEqual(rail['today'], [])

    def test_collaborator_inclusion(self):
        # Tasks where the user is a collaborator (not assignee) still appear.
        owner_project = create_project(name='Owner Project', user=self.other)
        t = create_task(
            project=owner_project, user=self.other,
            title='collab task', due_date=self.today, assignee=self.other,
        )
        # Add user as collaborator on the task
        from tasks.services import add_collaborator
        add_collaborator(task=t, user=self.other, target_user=self.user)
        rail = get_user_deadline_rail(self.user)
        self.assertIn('collab task', [x.title for x in rail['today']])

    def test_undated_task_count(self):
        self._task('no due', None)
        self._task('with due', self.today)
        self.assertEqual(get_user_undated_count(self.user), 1)

    def test_open_task_count_excludes_done(self):
        self._task('open', self.today)
        self._task('done', self.today - timedelta(days=1), status=Task.Status.DONE)
        self.assertEqual(get_user_open_task_count(self.user), 1)


@override_settings(HELM_TASKS_ENABLED=True)
class TasksByProjectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dok', email='dok@t.local')
        self.today = timezone.localdate()
        self.alpha = create_project(name='Alpha', user=self.user)
        self.bravo = create_project(name='Bravo', user=self.user)
        self.charlie = create_project(name='Charlie', user=self.user)

    def _task(self, project, title, due_date=None, status=Task.Status.TODO):
        t = create_task(
            project=project, user=self.user, title=title,
            due_date=due_date, assignee=self.user,
        )
        if status != t.status:
            t.status = status
            t.save(update_fields=['status'])
        return t

    def test_tasks_grouped_by_project(self):
        self._task(self.alpha, 'a1', self.today + timedelta(days=2))
        self._task(self.alpha, 'a2', self.today + timedelta(days=5))
        self._task(self.bravo, 'b1', self.today + timedelta(days=1))
        groups = get_user_tasks_by_project(self.user)
        # Bravo first (sooner due date), then Alpha. No Charlie (no tasks).
        names = [g['project'].name for g in groups]
        self.assertEqual(names, ['Bravo', 'Alpha'])
        self.assertEqual([t.title for t in groups[0]['tasks']], ['b1'])
        self.assertEqual([t.title for t in groups[1]['tasks']], ['a1', 'a2'])

    def test_done_tasks_excluded(self):
        self._task(self.alpha, 'open', self.today)
        self._task(self.alpha, 'done', self.today, status=Task.Status.DONE)
        groups = get_user_tasks_by_project(self.user)
        self.assertEqual(len(groups), 1)
        self.assertEqual([t.title for t in groups[0]['tasks']], ['open'])

    def test_undated_tasks_sort_to_bottom_within_project(self):
        self._task(self.alpha, 'no due')
        self._task(self.alpha, 'soon', self.today + timedelta(days=1))
        groups = get_user_tasks_by_project(self.user)
        titles = [t.title for t in groups[0]['tasks']]
        self.assertEqual(titles, ['soon', 'no due'])

    def test_project_with_only_undated_tasks_sorts_after_dated_projects(self):
        self._task(self.alpha, 'undated only')
        self._task(self.bravo, 'has due', self.today + timedelta(days=10))
        groups = get_user_tasks_by_project(self.user)
        names = [g['project'].name for g in groups]
        self.assertEqual(names, ['Bravo', 'Alpha'])

    def test_other_users_tasks_excluded(self):
        other = User.objects.create_user(username='other', email='o@t.local')
        other_proj = create_project(name='Other', user=other)
        create_task(
            project=other_proj, user=other, title='not mine',
            assignee=other, due_date=self.today,
        )
        groups = get_user_tasks_by_project(self.user)
        self.assertEqual(groups, [])
