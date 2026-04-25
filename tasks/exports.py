"""CSV + PDF export for project status reports.

Two formats:

- **CSV** — task list joinable for budget reconciliation, deadline analysis,
  external dashboard import. Sanitized via ``keel.core.export.csv_safe`` to
  neutralize formula-injection (=cmd|, +cmd|, etc.).
- **PDF** — human-readable status report for procurement / executive review.
  Built programmatically with ReportLab Platypus (already in
  ``helm/requirements.txt``). No HTML template — Platypus story builds the
  flow directly so we get reliable pagination + table layout without the
  Pango/Cairo system deps WeasyPrint would require on Railway.

Both functions return ``HttpResponse`` ready to send to the client.
"""
from __future__ import annotations

import csv
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone

from keel.core.export import csv_safe


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
def project_to_csv(project) -> HttpResponse:
    """Stream a project's task list as CSV.

    Columns: Title, Status, Priority, Assignee, Due, Completed, Created, Updated.
    Every value is passed through ``csv_safe()`` to neutralize CSV formula
    injection (cells starting with =, +, -, @, tab, CR get a leading
    single-quote).
    """
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="{project.slug}-tasks.csv"'
    )
    writer = csv.writer(response)
    writer.writerow([
        'Task title', 'Status', 'Priority', 'Assignee', 'Due date',
        'Completed at', 'Created at', 'Updated at',
    ])
    for task in project.tasks.select_related('assignee').order_by('position'):
        writer.writerow([
            csv_safe(task.title),
            csv_safe(task.get_status_display()),
            csv_safe(task.get_priority_display()),
            csv_safe(task.assignee.email if task.assignee else ''),
            task.due_date.isoformat() if task.due_date else '',
            task.completed_at.isoformat() if task.completed_at else '',
            task.created_at.isoformat(),
            task.updated_at.isoformat(),
        ])
    return response


# ---------------------------------------------------------------------------
# PDF export — ReportLab Platypus
# ---------------------------------------------------------------------------
def project_to_pdf(project) -> HttpResponse:
    """Render project status report as PDF via ReportLab Platypus.

    Sections:
      1. Header — project name, status pill, kind, generated timestamp.
      2. Summary table — created, started, target end, completed, archived,
         lead.
      3. Open tasks — Title / Status / Priority / Assignee / Due, capped at
         200 rows.
      4. Completed tasks — compact list with completion date, capped at 50.
      5. Recent status transitions — last 20 ProjectStatusHistory rows.
      6. Footer.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=f'{project.name} — Status Report',
    )
    styles = getSampleStyleSheet()
    h1 = styles['Title']
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], spaceBefore=12)
    body = styles['BodyText']
    small = ParagraphStyle(
        'small', parent=body, fontSize=8, textColor=colors.grey,
    )

    story = []

    # 1. Header
    story.append(Paragraph(project.name, h1))
    header_line = (
        f'Status: <b>{project.get_status_display()}</b> &nbsp;|&nbsp; '
        f'Kind: {project.get_kind_display()} &nbsp;|&nbsp; '
        f'Generated {timezone.now():%Y-%m-%d %H:%M UTC}'
    )
    story.append(Paragraph(header_line, body))
    if project.description:
        story.append(Spacer(1, 6))
        story.append(Paragraph(project.description, body))
    story.append(Spacer(1, 12))

    # 2. Summary table
    summary_rows = [
        ['Created',     project.created_at.strftime('%Y-%m-%d')],
        ['Started',     project.started_at.strftime('%Y-%m-%d') if project.started_at else '—'],
        ['Target end',  project.target_end_at.strftime('%Y-%m-%d') if project.target_end_at else '—'],
        ['Completed',   project.completed_at.strftime('%Y-%m-%d') if project.completed_at else '—'],
        ['Archived',    project.archived_at.strftime('%Y-%m-%d') if project.archived_at else '—'],
    ]
    assignment = (project.assignments
                         .filter(status='in_progress')
                         .select_related('assigned_to')
                         .first())
    if assignment and assignment.assigned_to:
        lead_label = (
            assignment.assigned_to.get_full_name()
            or assignment.assigned_to.email
            or assignment.assigned_to.username
        )
        summary_rows.append(['Lead', lead_label])
    t = Table(summary_rows, colWidths=[1.2 * inch, 4.5 * inch])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # 3. Open tasks
    open_tasks = (project.tasks
                         .exclude(status='done')
                         .select_related('assignee')
                         .order_by('position')[:200])
    if open_tasks:
        story.append(Paragraph('Open tasks', h2))
        rows = [['Title', 'Status', 'Priority', 'Assignee', 'Due']]
        for t_ in open_tasks:
            rows.append([
                t_.title[:60],
                t_.get_status_display(),
                t_.get_priority_display(),
                (t_.assignee.email if t_.assignee else '—'),
                t_.due_date.strftime('%Y-%m-%d') if t_.due_date else '—',
            ])
        tbl = Table(
            rows,
            colWidths=[2.6 * inch, 1.0 * inch, 0.8 * inch, 1.6 * inch, 0.7 * inch],
        )
        tbl.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(tbl)

    # 4. Completed tasks (compact)
    done_tasks = project.tasks.filter(status='done').order_by('-completed_at')
    done_count = done_tasks.count()
    if done_count:
        story.append(Spacer(1, 8))
        story.append(Paragraph(f'Completed tasks ({done_count})', h2))
        for t_ in done_tasks[:50]:
            stamp = t_.completed_at.strftime('%Y-%m-%d') if t_.completed_at else '—'
            story.append(Paragraph(
                f'✓ {t_.title} <font color="#666">— {stamp}</font>', small,
            ))

    # 5. Status history (last 20)
    history = (project.status_history
                      .select_related('changed_by')
                      .order_by('-changed_at')[:20])
    history_list = list(history)
    if history_list:
        story.append(Spacer(1, 8))
        story.append(Paragraph('Recent status transitions', h2))
        for h in history_list:
            who = (h.changed_by.email if h.changed_by else 'system')
            stamp = h.changed_at.strftime('%Y-%m-%d %H:%M')
            story.append(Paragraph(
                f'{stamp} — {h.old_status} → <b>{h.new_status}</b> by {who}',
                small,
            ))

    # 6. Footer
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        'Generated by Helm — DockLabs Project Management', small,
    ))

    doc.build(story)
    pdf = buf.getvalue()
    buf.close()
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="{project.slug}-status-report.pdf"'
    )
    return response
