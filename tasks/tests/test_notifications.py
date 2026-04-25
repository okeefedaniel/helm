"""Phase 6 notification tests.

Pins:
- All 13 helm_* notification types are registered in the keel registry.
- notify() actually fires when the corresponding service runs (in_app row created).
- Recipient resolvers correctly pick lead + active collaborators with the
  notify_on_status / notify_on_notes opt-out fields honored.
- Email channel renders the right template with the expected subject.
- External-collaborator invite uses the direct send_mail path (no User row).
- TaskComment post_save signal fires helm_task_comment_added.
"""
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

from keel.notifications.registry import get_all_types

from core.models import Notification
from tasks.models import (
    ProjectCollaborator, Task, TaskComment,
)
from tasks.services import (
    add_project_collaborator, add_project_note, archive_project,
    claim_project, create_project, create_task, transition_project,
    transition_task,
)

User = get_user_model()


@override_settings(HELM_TASKS_ENABLED=True)
class RegistrationTests(TestCase):
    def test_all_13_helm_types_registered(self):
        helm_types = {k: v for k, v in get_all_types().items() if k.startswith('helm_')}
        expected = {
            'helm_project_assigned',
            'helm_project_collaborator_invited',
            'helm_project_collaborator_invited_external',
            'helm_project_status_changed',
            'helm_project_archived',
            'helm_project_unarchived',
            'helm_project_note_added',
            'helm_project_attachment_added',
            'helm_task_assigned',
            'helm_task_status_changed',
            'helm_task_comment_added',
            'helm_task_due_soon',
            'helm_task_overdue',
        }
        self.assertEqual(set(helm_types.keys()), expected)

    def test_external_invite_is_email_only(self):
        t = get_all_types()['helm_project_collaborator_invited_external']
        self.assertEqual(t.default_channels, ['email'])

    def test_email_types_have_templates(self):
        types_with_email = [
            t for t in get_all_types().values()
            if t.key.startswith('helm_') and 'email' in t.default_channels
            and t.key != 'helm_project_collaborator_invited_external'
        ]
        # Each must have an email_template path set.
        for t in types_with_email:
            self.assertIsNotNone(t.email_template, f'{t.key} missing email_template')


@override_settings(HELM_TASKS_ENABLED=True)
class ProjectAssignedNotificationTests(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username='lead', email='lead@t.local')
        self.manager = User.objects.create_user(
            username='mgr', email='mgr@t.local', is_staff=True,
        )
        self.project = create_project(name='Test', user=self.manager)
        Notification.objects.all().delete()
        mail.outbox = []

    def test_manager_assigned_claim_notifies_new_lead(self):
        claim_project(project=self.project, user=self.lead, by_manager=self.manager)
        # In-app notification to the new lead.
        notifs = Notification.objects.filter(recipient=self.lead)
        self.assertEqual(notifs.count(), 1)
        # Email sent.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.lead.email])
        self.assertIn('You are now leading', mail.outbox[0].subject)

    def test_self_claim_does_not_notify(self):
        # Self-claim — actor and recipient are the same person, no notify.
        claim_project(project=self.project, user=self.lead)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(HELM_TASKS_ENABLED=True)
class CollaboratorInviteNotificationTests(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username='lead', email='lead@t.local')
        self.invitee = User.objects.create_user(username='inv', email='inv@t.local')
        self.project = create_project(name='Collab Test', user=self.lead)
        Notification.objects.all().delete()
        mail.outbox = []

    def test_internal_invite_uses_notify_pipeline(self):
        add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.invitee,
        )
        self.assertEqual(Notification.objects.filter(recipient=self.invitee).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.invitee.email])

    def test_external_invite_sends_email_only(self):
        add_project_collaborator(
            project=self.project, user=self.lead, email='external@example.com',
        )
        # No in-app — there's no User row.
        self.assertEqual(Notification.objects.count(), 0)
        # Email delivered.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['external@example.com'])
        self.assertIn(self.project.name, mail.outbox[0].subject)
        # Body should NOT mention magic link / token.
        body = mail.outbox[0].body + (mail.outbox[0].alternatives[0][0] if mail.outbox[0].alternatives else '')
        self.assertNotIn('token', body.lower())
        self.assertNotIn('magic', body.lower())


@override_settings(HELM_TASKS_ENABLED=True)
class FollowerNotificationTests(TestCase):
    """Pins recipient resolution + opt-out honoring on note/status events."""

    def setUp(self):
        self.lead = User.objects.create_user(username='lead', email='lead@t.local')
        self.notes_collab = User.objects.create_user(username='nc', email='nc@t.local')
        self.status_collab = User.objects.create_user(username='sc', email='sc@t.local')
        self.opt_out = User.objects.create_user(username='oo', email='oo@t.local')
        self.project = create_project(name='Follow Test', user=self.lead)
        claim_project(project=self.project, user=self.lead)
        # Three collaborators with different opt-outs.
        c1 = add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.notes_collab,
        )
        c1.notify_on_notes = True
        c1.notify_on_status = False
        c1.save(update_fields=['notify_on_notes', 'notify_on_status'])

        c2 = add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.status_collab,
        )
        c2.notify_on_notes = False
        c2.notify_on_status = True
        c2.save(update_fields=['notify_on_notes', 'notify_on_status'])

        c3 = add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.opt_out,
        )
        c3.notify_on_notes = False
        c3.notify_on_status = False
        c3.save(update_fields=['notify_on_notes', 'notify_on_status'])

        Notification.objects.all().delete()
        mail.outbox = []

    def test_note_added_notifies_only_notes_subscribers(self):
        # Action by lead (the actor — keel.notify excludes actor from recipients).
        add_project_note(
            project=self.project, user=self.lead, content='Diligence note',
        )
        recipients = set(
            Notification.objects.filter(
                title__contains='New Project Note',
            ).values_list('recipient', flat=True)
        )
        # notes_collab subscribed; the other two opted out; lead is actor so excluded.
        self.assertNotIn(self.lead.id, recipients)
        self.assertIn(self.notes_collab.id, recipients)
        self.assertNotIn(self.status_collab.id, recipients)
        self.assertNotIn(self.opt_out.id, recipients)

    def test_status_change_notifies_only_status_subscribers(self):
        transition_project(
            project=self.project, user=self.lead, target_status='on_hold',
            comment='Pause',
        )
        recipients = set(
            Notification.objects.filter(
                title__contains='Project Status Changed',
            ).values_list('recipient', flat=True)
        )
        # status_collab subscribed; notes_collab + opt_out skipped; lead is actor.
        self.assertNotIn(self.lead.id, recipients)
        self.assertIn(self.status_collab.id, recipients)
        self.assertNotIn(self.notes_collab.id, recipients)
        self.assertNotIn(self.opt_out.id, recipients)


@override_settings(HELM_TASKS_ENABLED=True)
class TaskNotificationTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(username='c', email='c@t.local')
        self.assignee = User.objects.create_user(username='a', email='a@t.local')
        self.project = create_project(name='Task Test', user=self.creator)
        Notification.objects.all().delete()
        mail.outbox = []

    def test_create_task_with_assignee_notifies(self):
        task = create_task(
            project=self.project, title='Do the thing',
            user=self.creator, assignee=self.assignee,
        )
        self.assertEqual(
            Notification.objects.filter(recipient=self.assignee).count(), 1,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.assignee.email])

    def test_create_task_without_assignee_no_notify(self):
        create_task(project=self.project, title='Unassigned', user=self.creator)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_task_comment_signal_fires_notification(self):
        task = create_task(
            project=self.project, title='X', user=self.creator,
            assignee=self.assignee,
        )
        Notification.objects.all().delete()
        mail.outbox = []
        # Direct creation (mirrors how the view does it).
        TaskComment.objects.create(
            task=task, author=self.creator, body='Hello',
        )
        # Assignee gets the notification (no opt-out set, defaults to True).
        recipients = set(
            Notification.objects.filter(
                title__contains='New Task Comment',
            ).values_list('recipient', flat=True)
        )
        self.assertIn(self.assignee.id, recipients)


@override_settings(HELM_TASKS_ENABLED=True)
class ArchiveNotificationTests(TestCase):
    def setUp(self):
        self.lead = User.objects.create_user(username='lead', email='lead@t.local')
        # Add a collaborator who is NOT the actor — so they receive the notify
        # (notify() excludes the actor from recipients).
        self.collab = User.objects.create_user(username='c', email='c@t.local')
        self.project = create_project(name='Archive', user=self.lead)
        claim_project(project=self.project, user=self.lead)
        add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.collab,
        )
        transition_project(
            project=self.project, user=self.lead, target_status='completed',
        )
        Notification.objects.all().delete()
        mail.outbox = []

    def test_archive_fires_helm_project_archived(self):
        archive_project(project=self.project, user=self.lead)
        # Collaborator (non-actor) receives the notification.
        self.assertEqual(
            Notification.objects.filter(
                title__contains='Project Archived',
                recipient=self.collab,
            ).count(),
            1,
        )
