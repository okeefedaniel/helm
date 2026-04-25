"""ADD-5 — Microsoft Project Online import wizard tests."""
import io

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from keel.accounts.models import ProductAccess

from tasks.integrations import project_online
from tasks.models import Project

User = get_user_model()


def _make_user(username='u', is_staff=False):
    u = User.objects.create_user(
        username=username, password='pw1234567890',
        email=f'{username}@t.local', is_staff=is_staff,
    )
    ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
    return u


CSV_BASIC = (
    'Project Name,Description,Start,Finish,Notes,Owner\n'
    'Bridge Replacement,Repair span,2026-04-01,2026-09-30,Quarterly review,Alice\n'
    'Annex Reroof,,2026-05-15,,,\n'
    ',Missing name row,2026-06-01,2026-07-01,,\n'
)


class ParseCsvTests(TestCase):
    def test_parses_rows_and_maps_columns(self):
        report = project_online.parse_csv(CSV_BASIC.encode('utf-8'))
        self.assertEqual(len(report.rows), 3)
        self.assertEqual(report.valid_count, 2)
        self.assertEqual(report.error_count, 1)
        # First row fully populated.
        r0 = report.rows[0]
        self.assertEqual(r0.name, 'Bridge Replacement')
        self.assertEqual(r0.description, 'Repair span')
        self.assertEqual(r0.started_at.isoformat(), '2026-04-01')
        self.assertEqual(r0.target_end_at.isoformat(), '2026-09-30')
        self.assertEqual(r0.notes_body, 'Quarterly review')
        self.assertEqual(r0.owner_label, 'Alice')
        # Third row missing name → error.
        self.assertFalse(report.rows[2].is_valid)
        self.assertIn('Missing project name', report.rows[2].errors[0])

    def test_column_mapping_includes_known_aliases(self):
        report = project_online.parse_csv(CSV_BASIC.encode('utf-8'))
        # Source column → mapped Helm field.
        self.assertEqual(report.column_mapping.get('Project Name'), 'name')
        self.assertEqual(report.column_mapping.get('Description'), 'description')
        self.assertEqual(report.column_mapping.get('Start'), 'started_at')
        self.assertEqual(report.column_mapping.get('Finish'), 'target_end_at')

    def test_handles_bom_prefixed_csv(self):
        content = ('﻿' + CSV_BASIC).encode('utf-8-sig')
        report = project_online.parse_csv(content)
        self.assertEqual(len(report.rows), 3)

    def test_handles_alternate_date_formats(self):
        content = (
            'Name,Start,Finish\n'
            'A,4/1/2026,9/30/2026\n'
        ).encode('utf-8')
        report = project_online.parse_csv(content)
        self.assertEqual(report.rows[0].started_at.isoformat(), '2026-04-01')


@override_settings(HELM_TASKS_ENABLED=True)
class CommitImportTests(TestCase):
    def setUp(self):
        self.user = _make_user('admin', is_staff=True)

    def test_commit_creates_projects_for_valid_rows(self):
        report = project_online.parse_csv(CSV_BASIC.encode('utf-8'))
        result = project_online.commit_import(report, user=self.user)
        self.assertEqual(result['created_count'], 2)
        self.assertEqual(result['skipped_count'], 1)
        self.assertEqual(result['failed_count'], 0)
        names = sorted(Project.objects.values_list('name', flat=True))
        self.assertIn('Bridge Replacement', names)
        self.assertIn('Annex Reroof', names)

    def test_commit_attaches_notes_when_provided(self):
        report = project_online.parse_csv(CSV_BASIC.encode('utf-8'))
        project_online.commit_import(report, user=self.user)
        bridge = Project.objects.get(name='Bridge Replacement')
        self.assertEqual(bridge.notes.count(), 1)
        self.assertIn('Quarterly review', bridge.notes.first().content)


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ImportEndpointTests(TestCase):
    def test_non_staff_gets_403(self):
        u = _make_user('peon', is_staff=False)
        self.client.force_login(u)
        r = self.client.get(reverse('tasks:import_project_online'))
        self.assertEqual(r.status_code, 403)

    def test_staff_sees_upload_form(self):
        u = _make_user('admin', is_staff=True)
        self.client.force_login(u)
        r = self.client.get(reverse('tasks:import_project_online'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Project Online')

    def test_post_file_renders_preview(self):
        u = _make_user('admin', is_staff=True)
        self.client.force_login(u)
        upload = io.BytesIO(CSV_BASIC.encode('utf-8'))
        upload.name = 'export.csv'
        r = self.client.post(
            reverse('tasks:import_project_online'),
            {'file': upload},
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Bridge Replacement')
        self.assertContains(r, 'ready to import')
        # No projects created yet.
        self.assertEqual(Project.objects.count(), 0)

    def test_commit_creates_projects(self):
        u = _make_user('admin', is_staff=True)
        self.client.force_login(u)
        upload = io.BytesIO(CSV_BASIC.encode('utf-8'))
        upload.name = 'export.csv'
        r = self.client.post(
            reverse('tasks:import_project_online'),
            {'file': upload, 'commit': '1'},
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Import complete')
        self.assertEqual(Project.objects.count(), 2)
