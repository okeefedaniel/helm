"""ADD-2 — FOIA statutory clock tests.

Pins:
- Federal 20-business-day deadline math respects weekends + holidays.
- Tolling additively extends the deadline.
- urgency_tier picks the right color tier per days_remaining.
- promote_fleet_item_to_task auto-creates the 3 default FOIA stage tasks
  AND populates the clock from received_at.
- toll/untoll endpoints respect ACL + workflow_view error mapping.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from keel.accounts.models import ProductAccess

from tasks.foia import (
    add_business_days, business_days_between, compute_statutory_deadline,
    days_remaining, is_business_day, recompute_deadline, urgency_tier,
)
from tasks.models import Project, Task
from tasks.services import (
    claim_project, create_project, promote_fleet_item_to_task,
    toll_foia, untoll_foia,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Business-day math
# ---------------------------------------------------------------------------
class BusinessDayMathTests(TestCase):
    def test_weekday_is_business_day(self):
        # 2026-04-27 is Monday.
        self.assertTrue(is_business_day(date(2026, 4, 27)))

    def test_saturday_is_not_business_day(self):
        self.assertFalse(is_business_day(date(2026, 4, 25)))

    def test_federal_holiday_is_not_business_day(self):
        # 2026-05-25 is Memorial Day.
        self.assertFalse(is_business_day(date(2026, 5, 25)))

    def test_ct_holiday_is_not_business_day_with_ct_holidays(self):
        # Lincoln's Birthday (Feb 12, 2026 = Thursday) is a CT state holiday
        # but NOT a federal holiday. Verify the jurisdiction-aware lookup.
        from tasks.foia import holidays_for
        ct = holidays_for('connecticut')
        fed = holidays_for('federal')
        self.assertFalse(is_business_day(date(2026, 2, 12), ct))
        self.assertTrue(is_business_day(date(2026, 2, 12), fed))

    def test_ct_good_friday_is_not_business_day(self):
        # Good Friday 2026 = Apr 3 (Easter = Apr 5). CT-only holiday.
        from tasks.foia import holidays_for
        ct = holidays_for('connecticut')
        self.assertFalse(is_business_day(date(2026, 4, 3), ct))

    def test_add_business_days_skips_weekend(self):
        # Friday + 1 BD = following Monday.
        result = add_business_days(date(2026, 4, 24), 1)  # Fri
        self.assertEqual(result, date(2026, 4, 27))  # Mon

    def test_add_business_days_skips_holiday(self):
        # Friday before Memorial Day weekend (May 22 = Fri) + 1 BD =
        # Tuesday May 26 (Mon May 25 is holiday).
        result = add_business_days(date(2026, 5, 22), 1)
        self.assertEqual(result, date(2026, 5, 26))

    def test_add_business_days_zero_returns_start(self):
        d = date(2026, 4, 27)
        self.assertEqual(add_business_days(d, 0), d)

    def test_business_days_between_excludes_endpoints(self):
        # Mon → Wed = 1 BD between (Tue only).
        result = business_days_between(date(2026, 4, 27), date(2026, 4, 29))
        self.assertEqual(result, 1)

    def test_business_days_between_handles_weekend(self):
        # Fri → Mon = 0 BDs between (Sat + Sun excluded).
        result = business_days_between(date(2026, 4, 24), date(2026, 4, 27))
        self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# Deadline computation
# ---------------------------------------------------------------------------
class StatutoryDeadlineTests(TestCase):
    def test_federal_deadline_is_20_business_days(self):
        # 2026-04-01 (Wed) + 20 BD, no holidays in window = 2026-04-29 (Wed).
        # Counting: Apr 2,3,6,7,8,9,10,13,14,15,16,17,20,21,22,23,24,27,28,29
        deadline = compute_statutory_deadline(date(2026, 4, 1), jurisdiction='federal')
        self.assertEqual(deadline, date(2026, 4, 29))

    def test_unknown_jurisdiction_defaults_to_federal(self):
        d_known = compute_statutory_deadline(date(2026, 4, 1), jurisdiction='federal')
        d_unknown = compute_statutory_deadline(date(2026, 4, 1), jurisdiction='wat')
        self.assertEqual(d_known, d_unknown)

    def test_tolled_days_extend_the_deadline(self):
        base = compute_statutory_deadline(date(2026, 4, 1))
        tolled = compute_statutory_deadline(date(2026, 4, 1), tolled_days=5)
        # 5 business days later than the base.
        self.assertEqual(business_days_between(base, tolled), 4)

    def test_connecticut_deadline_is_4_business_days(self):
        # 2026-04-20 (Mon) + 4 BD, no CT holidays in window = 2026-04-24 (Fri).
        deadline = compute_statutory_deadline(date(2026, 4, 20), jurisdiction='connecticut')
        self.assertEqual(deadline, date(2026, 4, 24))

    def test_connecticut_deadline_skips_lincolns_birthday(self):
        # Received Mon 2026-02-09. CT holidays in window: Lincoln (Thu Feb 12),
        # Presidents Day (Mon Feb 16). 4 BD = Feb 10, 11, 13, 17 → deadline 2026-02-17.
        deadline = compute_statutory_deadline(date(2026, 2, 9), jurisdiction='connecticut')
        self.assertEqual(deadline, date(2026, 2, 17))

    def test_connecticut_deadline_skips_good_friday(self):
        # Received Mon 2026-03-30. Good Friday = Apr 3 (Fri). 4 BD = Mar 31,
        # Apr 1, 2, then skip Fri Apr 3 → Mon Apr 6. Deadline = 2026-04-06.
        deadline = compute_statutory_deadline(date(2026, 3, 30), jurisdiction='connecticut')
        self.assertEqual(deadline, date(2026, 4, 6))


# ---------------------------------------------------------------------------
# urgency_tier
# ---------------------------------------------------------------------------
class UrgencyTierTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', email='u@t.local')
        self.project = create_project(name='FOIA P', user=self.user, kind='foia')

    def _set_deadline(self, days_from_now):
        self.project.foia_statutory_deadline_at = date.today() + timedelta(days=days_from_now)
        self.project.save(update_fields=['foia_statutory_deadline_at'])

    def test_no_deadline_returns_none_tier(self):
        self.assertEqual(urgency_tier(self.project), 'none')

    def test_overdue_when_deadline_in_past(self):
        self._set_deadline(-3)
        self.assertEqual(urgency_tier(self.project), 'overdue')

    def test_tolled_takes_precedence(self):
        # Set up deadline 1 day out (would be urgent), then toll.
        self._set_deadline(1)
        today = date.today()
        self.project.foia_tolled_at = today
        self.project.foia_tolled_until = today + timedelta(days=7)
        self.project.save(update_fields=['foia_tolled_at', 'foia_tolled_until'])
        self.assertEqual(urgency_tier(self.project, today=today), 'tolled')


# ---------------------------------------------------------------------------
# Promote integration: auto-create 3 default tasks + populate clock
# ---------------------------------------------------------------------------
@override_settings(HELM_TASKS_ENABLED=True)
class PromoteFOIAClockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u', email='u@t.local')
        self.project = create_project(name='Inbox', user=self.user)

    def test_promote_admiralty_foia_creates_three_default_tasks(self):
        promote_fleet_item_to_task(
            project=self.project, user=self.user,
            title='FOIA: state records', priority=Task.Priority.HIGH,
            product_slug='admiralty', item_type='foia_request',
            item_id='FOIA-X', url='https://admiralty.docklabs.ai/foia/FOIA-X/',
            fleet_item={'received_at': '2026-04-20'},
        )
        self.project.refresh_from_db()
        # Original promote-task + 3 auto-created stage tasks.
        titles = set(self.project.tasks.values_list('title', flat=True))
        self.assertIn('Acknowledge receipt within 5 business days', titles)
        self.assertIn('Search responsive records', titles)
        self.assertIn('Release / withhold by statutory deadline', titles)

    def test_promote_populates_clock_fields_from_received_at(self):
        promote_fleet_item_to_task(
            project=self.project, user=self.user,
            title='FOIA: state records', priority=Task.Priority.HIGH,
            product_slug='admiralty', item_type='foia_request',
            item_id='FOIA-X', url='https://example.com/',
            fleet_item={'received_at': '2026-04-20', 'jurisdiction': 'federal'},
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.kind, Project.Kind.FOIA)
        self.assertEqual(self.project.foia_received_at, date(2026, 4, 20))
        self.assertEqual(self.project.foia_jurisdiction, 'federal')
        # Computed deadline = 2026-04-20 (Mon) + 20 BD = 2026-05-18 (Mon).
        # No holidays fall within the window. (Memorial Day = May 25.)
        self.assertEqual(self.project.foia_statutory_deadline_at, date(2026, 5, 18))

    def test_promote_defaults_to_connecticut_when_no_jurisdiction_supplied(self):
        # DECD posture: when Admiralty doesn't tell us which statute applies,
        # default to CT (4 BD acknowledgment), not federal (20 BD substantive).
        promote_fleet_item_to_task(
            project=self.project, user=self.user,
            title='FOIA: CT records', priority=Task.Priority.HIGH,
            product_slug='admiralty', item_type='foia_request',
            item_id='FOIA-CT', url='https://example.com/',
            fleet_item={'received_at': '2026-04-20'},
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.foia_jurisdiction, 'connecticut')
        # 2026-04-20 (Mon) + 4 BD = 2026-04-24 (Fri). No CT holidays in window.
        self.assertEqual(self.project.foia_statutory_deadline_at, date(2026, 4, 24))

    def test_promote_idempotent_does_not_duplicate_default_tasks(self):
        promote_fleet_item_to_task(
            project=self.project, user=self.user,
            title='FOIA req', priority=Task.Priority.HIGH,
            product_slug='admiralty', item_type='foia_request',
            item_id='X', url='https://example.com/',
            fleet_item={'received_at': '2026-04-20'},
        )
        first_count = self.project.tasks.count()
        # Second promote (e.g., re-sync from feed). Project is already FOIA
        # kind, so the auto-create branch shouldn't fire.
        promote_fleet_item_to_task(
            project=self.project, user=self.user,
            title='FOIA req again', priority=Task.Priority.MEDIUM,
            product_slug='admiralty', item_type='foia_request',
            item_id='X', url='https://example.com/',
            fleet_item={'received_at': '2026-04-20'},
        )
        # Only the new top-level task added; no duplicate stage tasks.
        self.assertEqual(self.project.tasks.count(), first_count + 1)


# ---------------------------------------------------------------------------
# Toll / untoll service + endpoints
# ---------------------------------------------------------------------------
@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class TollUntollTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='u', password='pw1234567890', email='u@t.local',
        )
        ProductAccess.objects.create(user=self.user, product='helm', role='helm_admin')
        self.client.force_login(self.user)

        self.project = create_project(
            name='FOIA tolling', user=self.user, kind='foia',
        )
        self.project.foia_received_at = date(2026, 4, 1)
        self.project.foia_jurisdiction = 'federal'
        self.project.save(update_fields=['foia_received_at', 'foia_jurisdiction'])
        recompute_deadline(self.project)
        claim_project(project=self.project, user=self.user)

    def test_toll_extends_deadline(self):
        original_deadline = self.project.foia_statutory_deadline_at
        toll_foia(
            project=self.project, user=self.user,
            tolled_at=date(2026, 4, 10),
            tolled_until=date(2026, 4, 17),  # 5 BD span (Sat/Sun excluded)
            comment='Awaiting clarification',
        )
        self.project.refresh_from_db()
        self.assertGreater(
            self.project.foia_statutory_deadline_at, original_deadline,
        )

    def test_toll_endpoint_requires_dates(self):
        r = self.client.post(reverse('tasks:foia_toll', args=[self.project.slug]), {})
        self.assertEqual(r.status_code, 400)

    def test_toll_endpoint_rejects_inverted_dates(self):
        r = self.client.post(reverse('tasks:foia_toll', args=[self.project.slug]), {
            'tolled_at': '2026-04-17',
            'tolled_until': '2026-04-10',
        })
        self.assertEqual(r.status_code, 400)

    def test_untoll_clears_tolling_and_restores_deadline(self):
        toll_foia(
            project=self.project, user=self.user,
            tolled_at=date(2026, 4, 10),
            tolled_until=date(2026, 4, 17),
        )
        tolled_deadline = self.project.foia_statutory_deadline_at
        untoll_foia(project=self.project, user=self.user)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.foia_tolled_at)
        self.assertIsNone(self.project.foia_tolled_until)
        # Deadline reverted to the pre-toll value.
        self.assertLess(
            self.project.foia_statutory_deadline_at, tolled_deadline,
        )

    def test_toll_on_non_foia_project_raises(self):
        std = create_project(name='Standard', user=self.user)
        with self.assertRaises(ValueError):
            toll_foia(
                project=std, user=self.user,
                tolled_at=date(2026, 4, 10),
                tolled_until=date(2026, 4, 17),
            )
