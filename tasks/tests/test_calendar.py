"""Phase 8 calendar tests.

Pins:
- The three CalendarEventTypes are registered with keel.calendar.
- /tasks/calendar/ renders for an authenticated user.
- /tasks/calendar/events.json includes only projects/tasks the user can see.
- Archived projects are excluded from the calendar (active only).
- Done tasks are excluded from the calendar (open only).
- /tasks/calendar.ics returns text/calendar with VEVENT entries.
- iCal export honors the same ACL as the JSON feed.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from keel.accounts.models import ProductAccess
from keel.calendar.registry import get_type as get_calendar_event_type

from tasks.calendar_events import (
    PROJECT_COMPLETED, PROJECT_TARGET_END, TASK_DUE,
)
from tasks.models import Task
from tasks.services import (
    archive_project, claim_project, create_project, create_task,
    transition_project,
)

User = get_user_model()


def _make_user(username='u'):
    u = User.objects.create_user(
        username=username, password='pw1234567890',
        email=f'{username}@t.local',
    )
    ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
    return u


@override_settings(HELM_TASKS_ENABLED=True)
class CalendarRegistrationTests(TestCase):
    def test_three_event_types_registered(self):
        self.assertIsNotNone(get_calendar_event_type(PROJECT_TARGET_END))
        self.assertIsNotNone(get_calendar_event_type(PROJECT_COMPLETED))
        self.assertIsNotNone(get_calendar_event_type(TASK_DUE))

    def test_event_types_have_helm_prefix(self):
        # Namespace check — keep the registry tidy across products.
        self.assertTrue(PROJECT_TARGET_END.startswith('helm.'))
        self.assertTrue(PROJECT_COMPLETED.startswith('helm.'))
        self.assertTrue(TASK_DUE.startswith('helm.'))


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class CalendarPageTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)

    def test_calendar_index_renders(self):
        r = self.client.get(reverse('tasks:calendar_index'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'helm-calendar')           # FullCalendar mount point
        self.assertContains(r, 'fullcalendar@6')          # CDN script tag
        self.assertContains(r, 'calendar/events.json')    # event source URL

    def test_calendar_index_requires_login(self):
        self.client.logout()
        r = self.client.get(reverse('tasks:calendar_index'))
        # @login_required redirects unauthenticated.
        self.assertEqual(r.status_code, 302)


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class CalendarEventsJSONTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.stranger = _make_user('stranger')
        self.client.force_login(self.user)
        today = timezone.localdate()

        # Visible to user — has tasks + target end.
        self.visible = create_project(
            name='Visible', user=self.user,
            target_end_at=today + timedelta(days=14),
        )
        claim_project(project=self.visible, user=self.user)
        create_task(
            project=self.visible, title='Open task with due',
            user=self.user, due_date=today + timedelta(days=3),
        )

        # FOIA project — should get a red border.
        self.foia = create_project(
            name='FOIA Test', user=self.user,
            kind='foia',
            target_end_at=today + timedelta(days=10),
        )

        # Hidden — created by stranger, user has no access.
        self.hidden = create_project(
            name='Hidden', user=self.stranger,
            target_end_at=today + timedelta(days=7),
        )
        claim_project(project=self.hidden, user=self.stranger)
        create_task(
            project=self.hidden, title='Stranger task',
            user=self.stranger, due_date=today + timedelta(days=4),
        )

    def _events(self):
        r = self.client.get(reverse('tasks:calendar_events_json'))
        self.assertEqual(r.status_code, 200)
        return r.json()['events']

    def test_visible_project_target_end_included(self):
        titles = {e['title'] for e in self._events()}
        self.assertIn('⛳ Visible', titles)

    def test_visible_open_task_included(self):
        titles = {e['title'] for e in self._events()}
        self.assertIn('Open task with due', titles)

    def test_inaccessible_project_excluded(self):
        titles = {e['title'] for e in self._events()}
        self.assertNotIn('⛳ Hidden', titles)
        self.assertNotIn('Stranger task', titles)

    def test_done_tasks_excluded(self):
        Task.objects.filter(project=self.visible).update(status='done')
        titles = {e['title'] for e in self._events()}
        self.assertNotIn('Open task with due', titles)

    def test_archived_projects_excluded(self):
        # Drive the visible project completed → archived.
        transition_project(
            project=self.visible, user=self.user, target_status='completed',
        )
        archive_project(project=self.visible, user=self.user)
        titles = {e['title'] for e in self._events()}
        # The archived project's target_end is gone from the calendar.
        self.assertNotIn('⛳ Visible', titles)

    def test_foia_project_has_red_border(self):
        events = self._events()
        foia_event = next(e for e in events if 'FOIA Test' in e['title'])
        self.assertEqual(foia_event.get('borderColor'), '#dc2626')

    def test_overdue_task_colored_red(self):
        # Make the visible task overdue.
        Task.objects.filter(project=self.visible).update(
            due_date=timezone.localdate() - timedelta(days=1),
        )
        events = self._events()
        task_event = next(
            e for e in events
            if e['title'] == 'Open task with due'
        )
        self.assertEqual(task_event['color'], '#dc2626')


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class CalendarICalExportTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)
        self.project = create_project(
            name='ICal Test', user=self.user,
            target_end_at=timezone.localdate() + timedelta(days=5),
        )
        claim_project(project=self.project, user=self.user)
        create_task(
            project=self.project, title='Task in ICS',
            user=self.user, due_date=timezone.localdate() + timedelta(days=2),
        )

    def test_ical_returns_text_calendar(self):
        r = self.client.get(reverse('tasks:calendar_ical'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/calendar', r['Content-Type'])
        self.assertIn('helm-pm.ics', r['Content-Disposition'])

    def test_ical_includes_project_target_end(self):
        r = self.client.get(reverse('tasks:calendar_ical'))
        body = r.content.decode('utf-8')
        self.assertIn('BEGIN:VCALENDAR', body)
        self.assertIn('Target end: ICal Test', body)

    def test_ical_includes_task_summary(self):
        r = self.client.get(reverse('tasks:calendar_ical'))
        self.assertIn(b'SUMMARY:Task in ICS', r.content)

    def test_ical_excludes_inaccessible_projects(self):
        stranger = _make_user('stranger')
        hidden = create_project(
            name='Hidden ICal', user=stranger,
            target_end_at=timezone.localdate() + timedelta(days=3),
        )
        r = self.client.get(reverse('tasks:calendar_ical'))
        body = r.content.decode('utf-8')
        self.assertNotIn('Hidden ICal', body)

    def test_ical_requires_login(self):
        self.client.logout()
        r = self.client.get(reverse('tasks:calendar_ical'))
        self.assertEqual(r.status_code, 302)
