"""FOIA exportable type registrations + statutory clock for Helm PM.

Registers Project, ProjectNote, and ProjectAttachment with the keel-shipped
``foia_export_registry``. Once registered, any of those records can be
pushed to the Admiralty queue via
``keel.foia.export.submit_to_foia('helm', record_type, record_id, ...)``.

The registration itself does NOT auto-export anything — it just makes
the records *eligible* for export via the queue. Manual exports happen
through Admiralty's UI or via the ``foia_audit`` management command.

This module is imported from ``TasksConfig.ready()`` and should remain
side-effect-only at module level (function definitions; no early DB
queries since AppConfig.ready() runs before app loading completes).
"""
from __future__ import annotations

from datetime import datetime

from keel.foia.export import FOIAExportRecord, foia_export_registry


PRODUCT = 'helm'


def _serialize_project(project) -> FOIAExportRecord:
    return FOIAExportRecord(
        source_product=PRODUCT,
        record_type='project',
        # Project.pk is BigAutoField; the registry's export_record() does
        # qs.get(pk=record_id) so we use the integer pk here. UUID
        # public_id is exposed in metadata for cross-product references.
        record_id=str(project.pk),
        title=project.name,
        content=project.description or '(no description)',
        created_by=(
            project.created_by.get_full_name() or project.created_by.username
            if project.created_by_id else ''
        ),
        created_at=project.created_at,
        metadata={
            'slug': project.slug,
            'public_id': str(project.public_id),
            'kind': project.kind,
            'status': project.status,
            'archived_at': project.archived_at.isoformat() if project.archived_at else None,
            'foia_metadata': project.foia_metadata or {},
        },
    )


def _serialize_project_note(note) -> FOIAExportRecord:
    return FOIAExportRecord(
        source_product=PRODUCT,
        record_type='project_note',
        record_id=str(note.id),
        title=f'Note on {note.project.name}',
        content=note.content,
        created_by=(
            note.author.get_full_name() or note.author.username
            if note.author_id else ''
        ),
        created_at=note.created_at,
        metadata={
            'project_slug': note.project.slug,
            'project_kind': note.project.kind,
            'is_internal': note.is_internal,
        },
    )


def _serialize_project_attachment(attachment) -> FOIAExportRecord:
    # Attachments don't have free-form text content the way notes do, so
    # we synthesize a content body that includes the description and
    # filename for FOIA reviewers to search against.
    body_lines = [
        f'Attachment: {attachment.filename}',
        f'Size: {attachment.size_bytes} bytes',
        f'Visibility: {attachment.get_visibility_display()}',
        f'Source: {attachment.get_source_display()}',
    ]
    if attachment.description:
        body_lines.append('')
        body_lines.append(attachment.description)
    return FOIAExportRecord(
        source_product=PRODUCT,
        record_type='project_attachment',
        record_id=str(attachment.id),
        title=f'{attachment.filename} ({attachment.project.name})',
        content='\n'.join(body_lines),
        created_by=(
            attachment.uploaded_by.get_full_name() or attachment.uploaded_by.username
            if attachment.uploaded_by_id else ''
        ),
        created_at=attachment.uploaded_at,
        metadata={
            'project_slug': attachment.project.slug,
            'project_kind': attachment.project.kind,
            'visibility': attachment.visibility,
            'source': attachment.source,
            'filename': attachment.filename,
            'content_type': attachment.content_type,
            'size_bytes': attachment.size_bytes,
            'manifest_packet_uuid': attachment.manifest_packet_uuid,
        },
    )


def register_all():
    """Register Helm's FOIA-exportable record types with the keel registry.

    Called from ``TasksConfig.ready()``. The queryset_fn callables are
    deferred (lambdas that import the model at call time) to avoid
    AppConfig-loading-order issues — the registry is populated at startup
    but querysets only resolve when a record is actually exported.
    """

    def project_qs():
        from tasks.models import Project
        return Project.objects.all()

    def project_note_qs():
        from tasks.models import ProjectNote
        return ProjectNote.objects.select_related('project', 'author')

    def project_attachment_qs():
        from tasks.models import ProjectAttachment
        return ProjectAttachment.objects.select_related('project', 'uploaded_by')

    foia_export_registry.register(
        product=PRODUCT,
        record_type='project',
        queryset_fn=project_qs,
        serializer_fn=_serialize_project,
        display_name='Helm Project',
        description='A project (any kind) tracked in Helm Project Management.',
    )
    foia_export_registry.register(
        product=PRODUCT,
        record_type='project_note',
        queryset_fn=project_note_qs,
        serializer_fn=_serialize_project_note,
        display_name='Helm Project Note',
        description='A diligence note recorded against a Helm project.',
    )
    foia_export_registry.register(
        product=PRODUCT,
        record_type='project_attachment',
        queryset_fn=project_attachment_qs,
        serializer_fn=_serialize_project_attachment,
        display_name='Helm Project Attachment',
        description='A file attached to a Helm project.',
    )


# ---------------------------------------------------------------------------
# ADD-2 — FOIA statutory clock
# ---------------------------------------------------------------------------
# Federal FOIA = 20 business days (5 USC 552(a)(6)(A)(i)).
# Federal holidays observed for business-day math. Updated annually.
#
# Other jurisdictions are intentionally NOT in v1 — keel.deadlines is the
# right home for jurisdiction-aware deadline math, deferred per plan §13.
# When that lands, this module's compute_statutory_deadline() becomes a
# thin wrapper over keel.deadlines.compute_deadline().

from datetime import date, timedelta
from typing import Optional


# Federal holidays — embedded data table. Refresh annually.
# Source: 5 U.S. Code § 6103 (federal holiday observance rules: weekend
# holidays observed on the nearest weekday).
_FEDERAL_HOLIDAYS = {
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day (3rd Mon Jan)
    date(2026, 2, 16),   # Presidents Day (3rd Mon Feb)
    date(2026, 5, 25),   # Memorial Day (last Mon May)
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day observed (Jul 4 = Sat)
    date(2026, 9, 7),    # Labor Day (1st Mon Sep)
    date(2026, 10, 12),  # Columbus Day (2nd Mon Oct)
    date(2026, 11, 11),  # Veterans Day
    date(2026, 11, 26),  # Thanksgiving (4th Thu Nov)
    date(2026, 12, 25),  # Christmas Day
    # 2027
    date(2027, 1, 1),
    date(2027, 1, 18),   # MLK
    date(2027, 2, 15),   # Presidents
    date(2027, 5, 31),   # Memorial
    date(2027, 6, 18),   # Juneteenth observed (Jun 19 = Sat)
    date(2027, 7, 5),    # Independence observed (Jul 4 = Sun)
    date(2027, 9, 6),    # Labor
    date(2027, 10, 11),  # Columbus
    date(2027, 11, 11),  # Veterans
    date(2027, 11, 25),  # Thanksgiving
    date(2027, 12, 24),  # Christmas observed (Dec 25 = Sat)
}


_BUSINESS_DAYS_PER_JURISDICTION = {
    'federal': 20,
}


def is_business_day(d: date, holidays: set[date] = _FEDERAL_HOLIDAYS) -> bool:
    """Mon-Fri AND not in the holiday set."""
    return d.weekday() < 5 and d not in holidays


def add_business_days(start: date, n: int, holidays: set[date] = _FEDERAL_HOLIDAYS) -> date:
    """Return ``start`` + ``n`` business days, skipping weekends + holidays.

    Counting is exclusive of the start date. ``add_business_days(Monday, 1)``
    returns the following Tuesday (assuming no holiday).
    """
    cursor = start
    remaining = n
    while remaining > 0:
        cursor = cursor + timedelta(days=1)
        if is_business_day(cursor, holidays):
            remaining -= 1
    return cursor


def business_days_between(start: date, end: date, holidays: set[date] = _FEDERAL_HOLIDAYS) -> int:
    """Number of business days strictly between ``start`` and ``end``,
    exclusive of both endpoints. Negative if end < start.
    """
    if end == start:
        return 0
    sign = 1 if end > start else -1
    lo, hi = sorted((start, end))
    count = 0
    cursor = lo + timedelta(days=1)
    while cursor < hi:
        if is_business_day(cursor, holidays):
            count += 1
        cursor = cursor + timedelta(days=1)
    return sign * count


def compute_statutory_deadline(
    received_at: date,
    *,
    jurisdiction: str = 'federal',
    tolled_days: int = 0,
) -> date:
    """Compute the statutory response deadline for a FOIA request.

    Federal: received_at + 20 business days. ``tolled_days`` extends the
    clock additively (e.g., a 5-day tolling adds 5 BDs to the deadline).

    Returns the deadline DATE — clients must compare against today() and
    handle the urgency tiers themselves.
    """
    bdays = _BUSINESS_DAYS_PER_JURISDICTION.get(jurisdiction)
    if bdays is None:
        # Unknown jurisdiction — fall back to federal but flag in logs.
        import logging
        logging.getLogger(__name__).warning(
            'Unknown FOIA jurisdiction %r; defaulting to federal 20BD', jurisdiction,
        )
        bdays = 20
    return add_business_days(received_at, bdays + tolled_days)


def days_remaining(
    project,
    today: Optional[date] = None,
) -> Optional[int]:
    """Business days between today and the deadline.

    Negative if past deadline. None if the project doesn't have a
    statutory deadline set or isn't a FOIA project.
    """
    if not project.foia_statutory_deadline_at:
        return None
    today = today or date.today()
    return business_days_between(today, project.foia_statutory_deadline_at)


def urgency_tier(project, today: Optional[date] = None) -> str:
    """Return a string tier for the countdown badge:
    'overdue' | 'urgent' | 'warning' | 'caution' | 'ok' | 'tolled' | 'none'.

    Used by the _foia_clock.html partial to pick a color.
    """
    if not project.foia_statutory_deadline_at:
        return 'none'
    today = today or date.today()
    # Tolled (paused) takes precedence over time-based urgency.
    if (project.foia_tolled_at
            and project.foia_tolled_until
            and project.foia_tolled_at <= today < project.foia_tolled_until):
        return 'tolled'
    days = days_remaining(project, today=today)
    if days is None:
        return 'none'
    if days < 0:
        return 'overdue'
    if days <= 2:
        return 'urgent'
    if days <= 5:
        return 'warning'
    if days <= 10:
        return 'caution'
    return 'ok'


def recompute_deadline(project) -> Optional[date]:
    """Recompute and persist ``foia_statutory_deadline_at`` from inputs.

    Returns the new deadline, or None if recomputation isn't possible
    (no received_at, or non-FOIA project).
    """
    if project.kind != project.Kind.FOIA or not project.foia_received_at:
        return None
    tolled_days = 0
    if project.foia_tolled_at and project.foia_tolled_until:
        tolled_days = business_days_between(
            project.foia_tolled_at, project.foia_tolled_until,
        )
        # Endpoints are exclusive; add 1 to include the tolled span.
        if tolled_days > 0:
            tolled_days += 1
    deadline = compute_statutory_deadline(
        project.foia_received_at,
        jurisdiction=project.foia_jurisdiction or 'federal',
        tolled_days=tolled_days,
    )
    project.foia_statutory_deadline_at = deadline
    project.save(update_fields=['foia_statutory_deadline_at'])
    return deadline
