"""Tests for tasks.queries — the dashboard's deadline-rail data source."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.models import Task
from tasks.queries import (
    get_user_deadline_rail,
    get_user_open_task_count,
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
