"""Phase 7 CSV/PDF export tests.

Pins:
- CSV: returns text/csv with the right filename and column headers.
- CSV: csv_safe() neutralizes formula-injection in task titles.
- PDF: returns application/pdf, content starts with %PDF magic bytes.
- PDF: filename matches the project slug.
- ACL: unauthorized users 404 on export endpoints.
- Audit: each export writes a 'export' AuditLog row.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from keel.accounts.models import ProductAccess

from core.models import AuditLog
from tasks.models import Task
from tasks.services import claim_project, create_project, create_task

User = get_user_model()


def _make_user(username='u', staff=False):
    u = User.objects.create_user(
        username=username, password='pw1234567890',
        email=f'{username}@t.local', is_staff=staff,
    )
    ProductAccess.objects.create(user=u, product='helm', role='helm_admin')
    return u


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ExportCSVTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)
        self.project = create_project(name='CSV Test', user=self.user)
        claim_project(project=self.project, user=self.user)
        create_task(
            project=self.project, title='First task',
            user=self.user, priority=Task.Priority.HIGH,
        )
        create_task(
            project=self.project, title='Second task',
            user=self.user, priority=Task.Priority.MEDIUM,
        )

    def test_csv_returns_text_csv_content_type(self):
        r = self.client.get(reverse('tasks:export_project_csv', args=[self.project.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])

    def test_csv_filename_uses_slug(self):
        r = self.client.get(reverse('tasks:export_project_csv', args=[self.project.slug]))
        self.assertIn(f'{self.project.slug}-tasks.csv', r['Content-Disposition'])

    def test_csv_includes_header_row_and_tasks(self):
        r = self.client.get(reverse('tasks:export_project_csv', args=[self.project.slug]))
        body = r.content.decode('utf-8')
        # Header row.
        self.assertIn('Task title,Status,Priority', body)
        # Task data.
        self.assertIn('First task', body)
        self.assertIn('Second task', body)

    def test_csv_neutralizes_formula_injection(self):
        # Task whose title would be a CSV formula payload if not sanitized.
        create_task(
            project=self.project, title='=cmd|/c calc!A1',
            user=self.user,
        )
        r = self.client.get(reverse('tasks:export_project_csv', args=[self.project.slug]))
        body = r.content.decode('utf-8')
        # csv_safe prefixes a single-quote so Excel/Sheets won't evaluate.
        self.assertIn("'=cmd|/c calc!A1", body)

    def test_csv_writes_audit_log(self):
        before = AuditLog.objects.filter(
            action='export',
            entity_type='helm_tasks.Project',
            entity_id=str(self.project.pk),
        ).count()
        self.client.get(reverse('tasks:export_project_csv', args=[self.project.slug]))
        after = AuditLog.objects.filter(
            action='export',
            entity_type='helm_tasks.Project',
            entity_id=str(self.project.pk),
        ).count()
        self.assertEqual(after, before + 1)

    def test_csv_unauthorized_user_gets_404(self):
        stranger = _make_user('stranger')
        self.client.logout()
        self.client.force_login(stranger)
        r = self.client.get(reverse('tasks:export_project_csv', args=[self.project.slug]))
        self.assertEqual(r.status_code, 404)


@override_settings(HELM_TASKS_ENABLED=True, ROOT_URLCONF='helm_site.urls')
class ExportPDFTests(TestCase):
    def setUp(self):
        self.user = _make_user('u')
        self.client.force_login(self.user)
        self.project = create_project(
            name='PDF Test',
            description='A project for PDF rendering verification.',
            user=self.user,
        )
        claim_project(project=self.project, user=self.user)
        create_task(
            project=self.project, title='Open task in PDF',
            user=self.user, priority=Task.Priority.HIGH,
        )
        # One done task to exercise the completed-section render.
        done = create_task(
            project=self.project, title='Done task in PDF',
            user=self.user,
        )
        done.status = Task.Status.DONE
        done.save(update_fields=['status'])

    def test_pdf_returns_application_pdf(self):
        r = self.client.get(reverse('tasks:export_project_pdf', args=[self.project.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')

    def test_pdf_starts_with_pdf_magic_bytes(self):
        r = self.client.get(reverse('tasks:export_project_pdf', args=[self.project.slug]))
        self.assertEqual(r.content[:4], b'%PDF')

    def test_pdf_filename_uses_slug(self):
        r = self.client.get(reverse('tasks:export_project_pdf', args=[self.project.slug]))
        self.assertIn(f'{self.project.slug}-status-report.pdf', r['Content-Disposition'])

    def test_pdf_writes_audit_log(self):
        before = AuditLog.objects.filter(
            action='export',
            entity_type='helm_tasks.Project',
            entity_id=str(self.project.pk),
        ).count()
        self.client.get(reverse('tasks:export_project_pdf', args=[self.project.slug]))
        after = AuditLog.objects.filter(
            action='export',
            entity_type='helm_tasks.Project',
            entity_id=str(self.project.pk),
        ).count()
        self.assertEqual(after, before + 1)

    def test_pdf_unauthorized_user_gets_404(self):
        stranger = _make_user('stranger')
        self.client.logout()
        self.client.force_login(stranger)
        r = self.client.get(reverse('tasks:export_project_pdf', args=[self.project.slug]))
        self.assertEqual(r.status_code, 404)

    def test_pdf_renders_when_no_tasks(self):
        empty = create_project(name='Empty', user=self.user)
        r = self.client.get(reverse('tasks:export_project_pdf', args=[empty.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content[:4], b'%PDF')
