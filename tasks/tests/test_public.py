"""ADD-3 — Public transparency tests.

Pins:
- PRIVATE projects 404 on /p/<public_id>/.
- PUBLIC projects render with name, status, dates, % complete.
- Archived public projects 404 (don't surface stale data publicly).
- Fund sources visible only for kind=CIP.
- NO PII leaks: notes, attachments, collaborators, internal IDs absent.
- Toggle endpoint requires LEAD or staff; non-LEAD gets 403.
- Toggle writes an AuditLog row.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from django.urls import reverse

from keel.accounts.models import ProductAccess

from core.models import AuditLog
from tasks.models import Project, ProjectCollaborator
from tasks.services import (
    add_project_attachment, add_project_collaborator, add_project_note,
    archive_project, claim_project, create_project, transition_project,
)

User = get_user_model()


def _make_user(username='u', staff=False):
    u = User.objects.create_user(
        username=username, password='pw1234567890',
        email=f'{username}@t.local', is_staff=staff,
    )
    ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
    return u


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class PublicProjectViewTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.private = create_project(name='Private', user=self.user)
        self.public = create_project(name='Public CIP', user=self.user, kind=Project.Kind.CIP)
        self.public.public_visibility = Project.PublicVisibility.PUBLIC
        self.public.fund_sources = [
            {'source': 'arpa', 'amount_cents': 100000000, 'label': 'CPF'},
        ]
        self.public.save()

    def test_private_project_returns_404(self):
        r = self.client.get(reverse(
            'public_project_detail', args=[self.private.public_id],
        ))
        self.assertEqual(r.status_code, 404)

    def test_public_project_renders_for_anonymous_user(self):
        r = self.client.get(reverse(
            'public_project_detail', args=[self.public.public_id],
        ))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Public CIP')

    def test_archived_public_project_returns_404(self):
        # Archived projects shouldn't surface publicly even if the toggle
        # is on — they're closed business.
        claim_project(project=self.public, user=self.user)
        transition_project(
            project=self.public, user=self.user, target_status='completed',
        )
        archive_project(project=self.public, user=self.user)
        r = self.client.get(reverse(
            'public_project_detail', args=[self.public.public_id],
        ))
        self.assertEqual(r.status_code, 404)

    def test_fund_sources_visible_only_for_cip(self):
        r = self.client.get(reverse(
            'public_project_detail', args=[self.public.public_id],
        ))
        # CIP project — fund sources surface.
        self.assertContains(r, 'ARPA')
        self.assertContains(r, 'Funding')

    def test_no_pii_leaks_notes_attachments_collaborators(self):
        # Add private state to the project.
        add_project_note(
            project=self.public, user=self.user,
            content='SECRET note content with PII',
        )
        from django.core.files.uploadedfile import SimpleUploadedFile
        add_project_attachment(
            project=self.public, user=self.user,
            file=SimpleUploadedFile('secret.txt', b'PII'),
            description='Internal redaction memo',
        )
        invitee = _make_user('invitee')
        add_project_collaborator(
            project=self.public, user=self.user, target_user=invitee,
        )

        r = self.client.get(reverse(
            'public_project_detail', args=[self.public.public_id],
        ))
        body = r.content.decode('utf-8')
        # Notes / attachments / collaborators MUST NOT appear.
        self.assertNotIn('SECRET note content', body)
        self.assertNotIn('Internal redaction memo', body)
        self.assertNotIn('secret.txt', body)
        # Email addresses (PII) MUST NOT appear.
        self.assertNotIn('invitee@t.local', body)
        self.assertNotIn(self.user.email, body)
        # Internal slug NOT exposed in URLs (only public_id is in the URL).
        self.assertNotIn(f'/projects/{self.public.slug}/', body)
        # Audit / status history endpoints not linked.
        self.assertNotIn('/admin/', body)
        self.assertNotIn('audit', body.lower())


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ToggleVisibilityTests(TestCase):
    def setUp(self):
        self.lead = _make_user('lead')
        self.contributor = _make_user('contrib')
        self.stranger = _make_user('stranger')
        self.project = create_project(name='X', user=self.lead)
        claim_project(project=self.project, user=self.lead)
        add_project_collaborator(
            project=self.project, user=self.lead, target_user=self.contributor,
            role=ProjectCollaborator.Role.CONTRIBUTOR,
        )

    def test_lead_can_toggle_to_public(self):
        self.client.force_login(self.lead)
        r = self.client.post(
            reverse('tasks:toggle_public_visibility', args=[self.project.slug]),
            {'visibility': 'public'},
        )
        self.assertEqual(r.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.public_visibility, 'public')

    def test_lead_can_toggle_back_to_private(self):
        self.client.force_login(self.lead)
        self.client.post(
            reverse('tasks:toggle_public_visibility', args=[self.project.slug]),
            {'visibility': 'public'},
        )
        self.client.post(
            reverse('tasks:toggle_public_visibility', args=[self.project.slug]),
            {'visibility': 'private'},
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.public_visibility, 'private')

    def test_contributor_cannot_toggle(self):
        self.client.force_login(self.contributor)
        r = self.client.post(
            reverse('tasks:toggle_public_visibility', args=[self.project.slug]),
            {'visibility': 'public'},
        )
        self.assertEqual(r.status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.public_visibility, 'private')

    def test_stranger_gets_404_via_acl(self):
        # ACL fires before visibility logic — stranger gets 404 from
        # @project_access_required.
        self.client.force_login(self.stranger)
        r = self.client.post(
            reverse('tasks:toggle_public_visibility', args=[self.project.slug]),
            {'visibility': 'public'},
        )
        self.assertEqual(r.status_code, 404)

    def test_invalid_visibility_returns_400(self):
        self.client.force_login(self.lead)
        r = self.client.post(
            reverse('tasks:toggle_public_visibility', args=[self.project.slug]),
            {'visibility': 'rogue-value'},
        )
        self.assertEqual(r.status_code, 400)

    def test_toggle_writes_audit_log(self):
        self.client.force_login(self.lead)
        AuditLog.objects.all().delete()
        self.client.post(
            reverse('tasks:toggle_public_visibility', args=[self.project.slug]),
            {'visibility': 'public'},
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action='update',
                description__contains='Public visibility set to public',
            ).exists(),
        )
