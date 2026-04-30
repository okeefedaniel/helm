"""Tests for tasks.queries — the dashboard's deadline-rail data source."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.models import Task
from tasks.queries import (
    get_user_deadline_rail,
    get_user_open_task_count,
    get_user_project_deadline_rail,
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
class ProjectDeadlineRailTests(TestCase):
    """Project-grouped variant used by the dashboard's My Work column."""

    def setUp(self):
        self.user = User.objects.create_user(username='dok', email='dok@t.local')
        self.today = timezone.localdate()

    def _task(self, project, title, due_date):
        return create_task(
            project=project, user=self.user, title=title,
            due_date=due_date, assignee=self.user,
        )

    def test_one_row_per_project_with_count(self):
        p = create_project(name='CIP North Haven', user=self.user)
        self._task(p, 'task-a', self.today)
        self._task(p, 'task-b', self.today + timedelta(days=2))
        self._task(p, 'task-c', self.today + timedelta(days=3))
        rail = get_user_project_deadline_rail(self.user)
        # Project lands in `today` (most urgent of its tasks) and rolls up
        # all three dated tasks into total_count.
        self.assertEqual(len(rail['today']), 1)
        self.assertEqual(rail['this_week'], [])
        entry = rail['today'][0]
        self.assertEqual(entry['project'], p)
        self.assertEqual(entry['total_count'], 3)

    def test_project_with_overdue_lands_in_overdue_bucket(self):
        # Same project carries one overdue task and one task next week — it
        # should appear once, in `overdue`, with total_count covering both.
        p = create_project(name='FOIA Case 42', user=self.user)
        self._task(p, 'overdue-thing', self.today - timedelta(days=2))
        self._task(p, 'next-week', self.today + timedelta(days=5))
        rail = get_user_project_deadline_rail(self.user)
        self.assertEqual(len(rail['overdue']), 1)
        self.assertEqual(rail['this_week'], [])
        entry = rail['overdue'][0]
        self.assertEqual(entry['total_count'], 2)
        self.assertTrue(entry['has_overdue'])
        # The "soonest" task driving urgency is the overdue one.
        self.assertEqual(entry['soonest_task'].title, 'overdue-thing')

    def test_soonest_task_is_min_due_date_within_bucket(self):
        p = create_project(name='Multi-task', user=self.user)
        self._task(p, 'later', self.today + timedelta(days=5))
        self._task(p, 'sooner', self.today + timedelta(days=2))
        rail = get_user_project_deadline_rail(self.user)
        self.assertEqual(len(rail['this_week']), 1)
        self.assertEqual(rail['this_week'][0]['soonest_task'].title, 'sooner')

    def test_distinct_projects_distinct_rows(self):
        p1 = create_project(name='Alpha', user=self.user)
        p2 = create_project(name='Beta', user=self.user)
        self._task(p1, 'a-today', self.today)
        self._task(p2, 'b-today', self.today)
        rail = get_user_project_deadline_rail(self.user)
        self.assertEqual(len(rail['today']), 2)
        self.assertEqual({e['project'].name for e in rail['today']}, {'Alpha', 'Beta'})

    def test_undated_tasks_omitted(self):
        p = create_project(name='Sparse', user=self.user)
        self._task(p, 'no-due', None)
        rail = get_user_project_deadline_rail(self.user)
        self.assertEqual(rail['overdue'], [])
        self.assertEqual(rail['today'], [])
        self.assertEqual(rail['this_week'], [])
        self.assertEqual(rail['upcoming'], [])

    def test_done_tasks_omitted_from_grouping(self):
        p = create_project(name='Mostly Done', user=self.user)
        done = self._task(p, 'done-overdue', self.today - timedelta(days=1))
        done.status = Task.Status.DONE
        done.save(update_fields=['status'])
        self._task(p, 'live-today', self.today)
        rail = get_user_project_deadline_rail(self.user)
        self.assertEqual(len(rail['today']), 1)
        self.assertEqual(rail['today'][0]['total_count'], 1)
        self.assertEqual(rail['overdue'], [])
