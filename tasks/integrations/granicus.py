"""ADD-7 — Granicus GovQA push hook.

Pushes a Helm FOIA project record to an existing Granicus GovQA workflow
when the customer already runs GovQA. Gated by ``GRANICUS_GOVQA_URL`` +
``GRANICUS_GOVQA_API_KEY`` env vars per the keel.CLAUDE.md "Deployment
Flexibility" pattern: the integration is invisible (button hidden,
endpoint 503) when env vars are unset. The internal
``keel.foia.export`` pipeline (Phase 9) is unaffected — both ship.

Posture: this is a PARTNERSHIP integration, not a displacement. Granicus
owns FOIA at 1/3 of the largest US cities. The capability in Helm's
demo says "we play nice with what you already have." Co-sell-ready.

What we send: project name, public_id, status, FOIA metadata
(request_id, agency, requester), statutory deadline, statutory clock
state. Granicus consumes this in their request-tracking surface.

What we DO NOT send: internal notes, attachments (Granicus has its own
records system), audit data, collaborator list.

Failure handling: best-effort POST with 1 retry; failures are
audit-logged and surface to the caller as a non-2xx return. The push
never blocks user-facing actions.

API contract: stubbed against a hypothetical GovQA REST shape
(``POST /api/v1/requests``). When a real customer engages, the
contract will need to be validated against the GovQA API spec they
provide; this module's serializer is the integration point.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from django.conf import settings


logger = logging.getLogger(__name__)


def is_available() -> bool:
    """Return True iff Granicus GovQA push is configured."""
    return bool(
        getattr(settings, 'GRANICUS_GOVQA_URL', '')
        and getattr(settings, 'GRANICUS_GOVQA_API_KEY', '')
    )


def _serialize_for_govqa(project) -> dict:
    """Build the JSON payload for the GovQA POST.

    Public-style serialization — same allowlist as the public transparency
    view PLUS FOIA metadata, since a Granicus customer's FOIA request
    record is the legitimate destination for that data.
    """
    payload = {
        'helm_public_id': str(project.public_id),
        'name': project.name,
        'status': project.status,
        'kind': project.kind,
        'started_at': project.started_at.isoformat() if project.started_at else None,
        'target_end_at': project.target_end_at.isoformat() if project.target_end_at else None,
    }
    if project.kind == project.Kind.FOIA:
        payload['foia'] = {
            'request_id': project.foia_metadata.get('foia_request_id', ''),
            'agency': project.foia_metadata.get('foia_agency', ''),
            'requester_organization': project.foia_metadata.get('foia_requester_organization', ''),
            'requester_name': project.foia_metadata.get('foia_requester_name', ''),
            'received_at': project.foia_received_at.isoformat() if project.foia_received_at else None,
            'statutory_deadline_at': (
                project.foia_statutory_deadline_at.isoformat()
                if project.foia_statutory_deadline_at else None
            ),
            'jurisdiction': project.foia_jurisdiction,
            'tolled_at': project.foia_tolled_at.isoformat() if project.foia_tolled_at else None,
            'tolled_until': project.foia_tolled_until.isoformat() if project.foia_tolled_until else None,
        }
    return payload


def push_to_govqa(project, *, user=None, timeout: float = 10.0) -> tuple[bool, Optional[str]]:
    """Push a project record to Granicus GovQA.

    Returns ``(success: bool, error_message: Optional[str])``. Best-effort:
    failures are logged but don't raise. ``is_available()`` should be
    checked by the caller before invoking.
    """
    if not is_available():
        return False, 'Granicus GovQA is not configured (GRANICUS_GOVQA_URL/API_KEY).'

    import requests

    url = settings.GRANICUS_GOVQA_URL.rstrip('/') + '/api/v1/requests'
    headers = {
        'Authorization': f'Bearer {settings.GRANICUS_GOVQA_API_KEY}',
        'Content-Type': 'application/json',
        'X-Source': 'helm-pm',
    }
    payload = _serialize_for_govqa(project)

    last_err = None
    for attempt in range(2):
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=timeout,
            )
            if 200 <= response.status_code < 300:
                _audit_push(project, user, success=True, response_status=response.status_code)
                return True, None
            last_err = f'HTTP {response.status_code}: {response.text[:200]}'
        except requests.RequestException as e:
            last_err = f'{type(e).__name__}: {e}'
            logger.warning(
                'GovQA push attempt %d failed for project %s: %s',
                attempt + 1, project.public_id, last_err,
            )

    _audit_push(project, user, success=False, error_message=last_err)
    return False, last_err


def _audit_push(project, user, *, success: bool, response_status: int = 0,
                error_message: str = '') -> None:
    """Write an audit entry for a push attempt — success or failure."""
    try:
        from keel.core.audit import log_audit
        action = 'export' if success else 'export'  # both are export-shape events
        description = (
            f'Pushed project {project.slug} to GovQA (HTTP {response_status})'
            if success
            else f'Failed to push project {project.slug} to GovQA: {error_message}'
        )
        log_audit(
            user=user if (user and getattr(user, 'is_authenticated', False)) else None,
            action=action,
            entity_type='helm_tasks.Project',
            entity_id=str(project.pk),
            description=description,
            changes={
                'integration': 'granicus_govqa',
                'success': success,
                'response_status': response_status,
                'error': error_message,
            },
            ip_address=getattr(user, 'audit_ip', None) if user else None,
        )
    except Exception:
        # Audit must never break the integration return path.
        logger.exception('Audit log write failed for GovQA push')
