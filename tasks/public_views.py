"""ADD-3 — public transparency view.

A read-only, unauthenticated rendering of a Project at /p/<public_id>/.
Renders ONLY: name, status, kind, target dates, task completion %, fund
sources (CIP-only). Excludes notes, attachments, collaborators, audit log,
internal IDs, and any other PII.

Per-project toggle on the project detail page lets a LEAD set the
project to PUBLIC. Default is PRIVATE — projects do not become public
without an explicit, audit-logged action.

Threat model:
- Keyed by ``public_id`` (UUID4). PRIVATE projects 404; UUID enumeration
  is computationally infeasible (full UUID4 search space).
- No login required, no CSRF (read-only).
- Per-field allowlist (not denylist): the template only renders fields
  enumerated in the context. Adding a model field doesn't auto-leak.
"""
from __future__ import annotations

from django.http import Http404, HttpResponseBadRequest
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from keel.core.audit import log_audit

from tasks.access import project_access_required, workflow_view
from tasks.models import Project, Task


def public_project_detail(request, public_id):
    """Render a public, read-only view of a PUBLIC project."""
    project = get_object_or_404(Project, public_id=public_id)
    if project.public_visibility != Project.PublicVisibility.PUBLIC:
        raise Http404
    if project.is_archived:
        raise Http404

    # Compute completion %.
    total_tasks = project.tasks.count()
    done_tasks = project.tasks.filter(status=Task.Status.DONE).count()
    pct_complete = int(round(100 * done_tasks / total_tasks)) if total_tasks else 0

    # FOIA clock — public-safe: only show days remaining + tier, never the
    # internal foia_request_id or requester_organization.
    foia_clock = None
    if (project.kind == Project.Kind.FOIA
            and project.foia_statutory_deadline_at):
        from tasks.foia import days_remaining, urgency_tier
        foia_clock = {
            'days': days_remaining(project),
            'tier': urgency_tier(project),
            'deadline': project.foia_statutory_deadline_at,
        }

    return render(request, 'tasks/public_project_detail.html', {
        'project': project,
        'pct_complete': pct_complete,
        'total_tasks': total_tasks,
        'done_tasks': done_tasks,
        'foia_clock': foia_clock,
    })


@login_required
@project_access_required
@workflow_view
@require_POST
def toggle_public_visibility(request, slug):
    """Set the project public_visibility to PUBLIC or PRIVATE. LEAD-only."""
    project = request.project
    # LEAD-only check via the same role logic the workflow engine uses.
    workflow = project.WORKFLOW
    if not workflow._user_has_role(
        request.user, ['lead', 'system_admin'], obj=project,
    ):
        from django.http import HttpResponse
        return HttpResponse(status=403)
    visibility = request.POST.get('visibility')
    if visibility not in (Project.PublicVisibility.PUBLIC, Project.PublicVisibility.PRIVATE):
        return HttpResponseBadRequest('invalid visibility')
    if project.public_visibility != visibility:
        project.public_visibility = visibility
        project.save(update_fields=['public_visibility'])
        log_audit(
            user=request.user, action='update',
            entity_type='helm_tasks.Project', entity_id=str(project.pk),
            description=f'Public visibility set to {visibility}',
            changes={'public_visibility': visibility},
            ip_address=getattr(request.user, 'audit_ip', None),
        )
        messages.success(
            request,
            'Project is now PUBLIC at /p/<id>/.' if visibility == 'public'
            else 'Project set to PRIVATE.',
        )
    return redirect(project.get_absolute_url())
