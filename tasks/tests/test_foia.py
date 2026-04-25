"""Phase 9 FOIA registration foundation tests.

Pins:
- Project, ProjectNote, ProjectAttachment are registered with
  keel.foia.export.foia_export_registry under product='helm'.
- The serializers return correctly shaped FOIAExportRecord instances.
- The Admiralty FOIA bridge in promote_fleet_item_to_task upgrades a
  STANDARD project to FOIA kind and populates foia_metadata.
- A real submit_to_foia call against the registered types creates a
  FOIAExportItem row in the helm queue.
"""
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from keel.foia.export import (
    foia_export_registry, submit_to_foia,
)

from core.models import FOIAExportItem
from tasks.models import Project, ProjectAttachment, ProjectNote
from tasks.services import (
    add_project_attachment, add_project_note, create_project,
    promote_fleet_item_to_task,
)

User = get_user_model()


@override_settings(HELM_TASKS_ENABLED=True)
class RegistryTests(TestCase):
    def test_project_registered(self):
        t = foia_export_registry.get_type('helm', 'project')
        self.assertIsNotNone(t)
        self.assertEqual(t.display_name, 'Helm Project')

    def test_project_note_registered(self):
        t = foia_export_registry.get_type('helm', 'project_note')
        self.assertIsNotNone(t)
        self.assertEqual(t.display_name, 'Helm Project Note')

    def test_project_attachment_registered(self):
        t = foia_export_registry.get_type('helm', 'project_attachment')
        self.assertIsNotNone(t)
        self.assertEqual(t.display_name, 'Helm Project Attachment')

    def test_three_helm_types_total(self):
        helm_types = foia_export_registry.get_exportable_types(product='helm')
        self.assertEqual(len(helm_types), 3)
        record_types = sorted(t.record_type for t in helm_types)
        self.assertEqual(record_types, ['project', 'project_attachment', 'project_note'])


@override_settings(HELM_TASKS_ENABLED=True)
class SerializerTests(TestCase):
    """Pin the serializer output shape — Admiralty's queue depends on it."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='u', email='u@t.local',
        )

    def test_project_serializer_returns_correct_shape(self):
        project = create_project(
            name='Q4 Audit', user=self.user,
            description='Internal audit of Q4 disbursements.',
        )
        rec = foia_export_registry.export_record(
            'helm', 'project', str(project.pk),
        )
        self.assertEqual(rec.source_product, 'helm')
        self.assertEqual(rec.record_type, 'project')
        self.assertEqual(rec.title, 'Q4 Audit')
        self.assertEqual(rec.content, 'Internal audit of Q4 disbursements.')
        self.assertEqual(rec.metadata['slug'], project.slug)
        self.assertEqual(rec.metadata['kind'], 'standard')
        # public_id is exposed in metadata for cross-product references.
        self.assertEqual(rec.metadata['public_id'], str(project.public_id))

    def test_project_note_serializer_returns_correct_shape(self):
        project = create_project(name='With note', user=self.user)
        note = add_project_note(
            project=project, user=self.user,
            content='Reviewed RFP scope with counsel.',
        )
        rec = foia_export_registry.export_record(
            'helm', 'project_note', str(note.id),
        )
        self.assertEqual(rec.record_type, 'project_note')
        self.assertIn('Reviewed RFP', rec.content)
        self.assertEqual(rec.metadata['project_slug'], project.slug)

    def test_project_attachment_serializer_returns_correct_shape(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        project = create_project(name='With file', user=self.user)
        f = SimpleUploadedFile('memo.txt', b'hello', content_type='text/plain')
        att = add_project_attachment(
            project=project, user=self.user, file=f,
            description='Counsel memo on scope',
        )
        rec = foia_export_registry.export_record(
            'helm', 'project_attachment', str(att.id),
        )
        self.assertEqual(rec.record_type, 'project_attachment')
        self.assertIn('memo.txt', rec.title)
        self.assertIn('Counsel memo', rec.content)
        self.assertEqual(rec.metadata['filename'], 'memo.txt')


@override_settings(HELM_TASKS_ENABLED=True)
class AdmiraltyBridgeTests(TestCase):
    """Promoting an Admiralty FOIA item upgrades the target project."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='exec', email='exec@t.local',
        )
        self.project = create_project(name='Default Project', user=self.user)

    def test_admiralty_foia_promotion_upgrades_project_kind(self):
        promote_fleet_item_to_task(
            project=self.project, user=self.user,
            title='FOIA: ARPA spring records',
            product_slug='admiralty', item_type='foia_request',
            item_id='FOIA-2026-0421',
            url='https://admiralty.docklabs.ai/foia/FOIA-2026-0421/',
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.kind, Project.Kind.FOIA)
        self.assertEqual(
            self.project.foia_metadata['foia_request_id'], 'FOIA-2026-0421',
        )
        self.assertEqual(
            self.project.foia_metadata['admiralty_url'],
            'https://admiralty.docklabs.ai/foia/FOIA-2026-0421/',
        )

    def test_promotion_with_full_fleet_item_lifts_metadata(self):
        promote_fleet_item_to_task(
            project=self.project, user=self.user,
            title='FOIA: state comptroller request',
            product_slug='admiralty', item_type='foia_request',
            item_id='FOIA-2026-0422',
            url='https://admiralty.docklabs.ai/foia/FOIA-2026-0422/',
            fleet_item={
                'received_at': '2026-04-20',
                'statutory_deadline': '2026-04-26',
                'agency': 'State Comptroller',
                'requester_organization': 'Hartford Courant',
                'requester_name': 'Jane Doe',
            },
        )
        self.project.refresh_from_db()
        meta = self.project.foia_metadata
        self.assertEqual(meta['foia_received_at'], '2026-04-20')
        self.assertEqual(meta['foia_statutory_deadline'], '2026-04-26')
        self.assertEqual(meta['foia_agency'], 'State Comptroller')

    def test_non_foia_promotion_leaves_kind_alone(self):
        promote_fleet_item_to_task(
            project=self.project, user=self.user,
            title='Harbor approval needed',
            product_slug='harbor', item_type='approval',
            item_id='APP-123',
            url='https://harbor.docklabs.ai/applications/APP-123/',
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.kind, Project.Kind.STANDARD)
        self.assertEqual(self.project.foia_metadata, {})

    def test_promotion_does_not_downgrade_existing_foia(self):
        """A project already FOIA-kind should stay FOIA, not have its
        foia_metadata overwritten by a non-FOIA promotion."""
        self.project.kind = Project.Kind.FOIA
        self.project.foia_metadata = {'foia_request_id': 'PREEXISTING'}
        self.project.save(update_fields=['kind', 'foia_metadata'])
        # A subsequent non-FOIA promotion shouldn't touch the metadata.
        promote_fleet_item_to_task(
            project=self.project, user=self.user,
            title='Harbor link',
            product_slug='harbor', item_type='approval',
            item_id='X', url='https://example.com/',
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.kind, Project.Kind.FOIA)
        self.assertEqual(
            self.project.foia_metadata['foia_request_id'], 'PREEXISTING',
        )


@override_settings(HELM_TASKS_ENABLED=True)
class SubmitToFOIATests(TestCase):
    """End-to-end: a real submit_to_foia call writes to the helm queue."""

    def test_submit_project_creates_export_item(self):
        user = User.objects.create_user(username='u', email='u@t.local')
        project = create_project(
            name='Audit project', user=user,
            description='Subject to FOIA review.',
        )
        item = submit_to_foia(
            source_product='helm',
            record_type='project',
            record_id=str(project.pk),
            title=project.name,
            content=project.description,
        )
        self.assertIsNotNone(item)
        self.assertEqual(
            FOIAExportItem.objects.filter(
                source_product='helm', record_type='project',
            ).count(),
            1,
        )
