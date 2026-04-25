"""FOIA exportable type registrations for Helm Project Management.

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
