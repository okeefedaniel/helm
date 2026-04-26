"""Business logic for Helm Tasks.

Keep views thin; do all mutations + audit logging here.

Two collaborator scopes coexist (per plan §6.2 / Phase 2):
- ``TaskCollaborator``: invited only to a specific task. Use
  ``add_task_collaborator`` / ``remove_task_collaborator``.
- ``ProjectCollaborator``: invited to the whole project (sees all tasks
  in it). Use ``add_project_collaborator`` / ``remove_project_collaborator``.

Project lifecycle services follow the DockLabs Project Lifecycle Standard:
``create_project`` → ``claim_project`` (Harbor reassign-on-conflict
semantics) → ``add_project_collaborator`` → ``add_project_note`` /
``add_project_attachment`` → ``transition_project`` → ``archive_project``
(writes both ``archived_at`` AND an ``ArchivedProjectRecord`` retention
row in one transaction.atomic) → ``unarchive_project`` (restores
``previous_terminal_status``).
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from keel.core.audit import log_audit
from keel.notifications import notify

from .models import (
    ArchivedProjectRecord,
    Project,
    ProjectAssignment,
    ProjectAttachment,
    ProjectCollaborator,
    ProjectNote,
    Task,
    TaskCollaborator,
    TaskLink,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _u(user):
    """Return user only if authenticated, else None — for audit/FK fields."""
    return user if (user and user.is_authenticated) else None


def _ip(user):
    return getattr(user, 'audit_ip', None)


# ---------------------------------------------------------------------------
# Task services (existing — kept; collaborator helpers renamed to be
# task-scoped explicitly so project-scoped equivalents read clearly)
# ---------------------------------------------------------------------------
@transaction.atomic
def create_task(*, project: Project, title: str, user, **fields) -> Task:
    position = (project.tasks.aggregate_max_position()
                if hasattr(project.tasks, 'aggregate_max_position')
                else (project.tasks.order_by('-position').values_list('position', flat=True).first() or 0) + 1)
    task = Task.objects.create(
        project=project,
        title=title,
        created_by=_u(user),
        position=position,
        **fields,
    )
    log_audit(
        user=_u(user),
        action='task.create',
        entity_type=task._meta.label,
        entity_id=str(task.pk),
        description=f'Created task "{title}" in {project.slug}',
        changes={'title': title, 'project': project.slug},
        ip_address=_ip(user),
    )
    if task.assignee_id and task.assignee_id != getattr(user, 'pk', None):
        notify(
            event='helm_task_assigned',
            actor=_u(user),
            context={'task': task, 'project': project, 'title': task.title},
        )
    return task


@transaction.atomic
def update_task(task: Task, *, user, **fields) -> Task:
    changed = {}
    for key, value in fields.items():
        if getattr(task, key, None) != value:
            changed[key] = value
            setattr(task, key, value)
    if 'status' in changed and changed['status'] == Task.Status.DONE and not task.completed_at:
        task.completed_at = timezone.now()
        changed['completed_at'] = task.completed_at
    elif 'status' in changed and changed['status'] != Task.Status.DONE:
        task.completed_at = None
        changed['completed_at'] = None
    if changed:
        task.save()
        log_audit(
            user=_u(user),
            action='task.update',
            entity_type=task._meta.label,
            entity_id=str(task.pk),
            description=f'Updated task fields: {", ".join(changed.keys())}',
            changes={'changed': list(changed.keys())},
            ip_address=_ip(user),
        )
        # Newly assigned to someone other than the actor → notify them.
        if 'assignee' in changed and task.assignee_id and task.assignee_id != getattr(user, 'pk', None):
            notify(
                event='helm_task_assigned',
                actor=_u(user),
                context={'task': task, 'project': task.project, 'title': task.title},
            )
        if 'status' in changed:
            notify(
                event='helm_task_status_changed',
                actor=_u(user),
                context={'task': task, 'project': task.project, 'title': task.title},
            )
    return task


@transaction.atomic
def reorder_task(task: Task, *, user, new_status: str, new_position: int) -> Task:
    task.status = new_status
    task.position = new_position
    if new_status == Task.Status.DONE and not task.completed_at:
        task.completed_at = timezone.now()
    elif new_status != Task.Status.DONE:
        task.completed_at = None
    task.save(update_fields=['status', 'position', 'completed_at', 'updated_at'])
    log_audit(
        user=_u(user),
        action='task.reorder',
        entity_type=task._meta.label,
        entity_id=str(task.pk),
        description=f'Reordered task to {new_status}#{new_position}',
        changes={'status': new_status, 'position': new_position},
        ip_address=_ip(user),
    )
    return task


@transaction.atomic
def transition_task(*, task: Task, user, target_status: str, comment: str = '') -> Task:
    """Engine-validated status transition. Wraps ``Task.transition()``.

    Side-effects: stamps ``completed_at`` when target is DONE; clears it
    when target moves out of DONE. Records ``TaskStatusHistory`` via the
    workflow engine's history hook.
    """
    task.transition(target_status, user=user, comment=comment)
    if target_status == Task.Status.DONE and not task.completed_at:
        task.completed_at = timezone.now()
        task.save(update_fields=['completed_at'])
    elif target_status != Task.Status.DONE and task.completed_at:
        task.completed_at = None
        task.save(update_fields=['completed_at'])
    log_audit(
        user=_u(user),
        action='task.transition',
        entity_type=task._meta.label,
        entity_id=str(task.pk),
        description=f'Transitioned task to {target_status}',
        changes={'target_status': target_status, 'comment': comment},
        ip_address=_ip(user),
    )
    notify(
        event='helm_task_status_changed',
        actor=_u(user),
        context={'task': task, 'project': task.project, 'title': task.title},
    )
    return task


@transaction.atomic
def promote_fleet_item_to_task(
    *,
    project: Project,
    user,
    title: str,
    product_slug: str,
    item_type: str,
    item_id: str,
    url: str,
    description: str = '',
    priority: str = Task.Priority.MEDIUM,
    fleet_item: dict | None = None,
) -> Task:
    """Create a task from a fleet action-queue item or alert.

    When the source is an Admiralty FOIA request and the target project is
    a STANDARD-kind project, the project is upgraded to FOIA kind and its
    ``foia_metadata`` is populated from the fleet_item payload. This is
    the Phase 9 Admiralty bridge: a Helm exec promoting a FOIA action item
    converts the project into a FOIA-tracked one without manual data entry.

    Optional ``fleet_item`` carries the full action-item dict from the
    Admiralty helm-feed (received_at, statutory_deadline, agency, etc.).
    """
    task = create_task(
        project=project,
        title=title,
        description=description,
        priority=priority,
        user=user,
    )
    TaskLink.objects.create(
        task=task,
        product_slug=product_slug,
        item_type=item_type,
        item_id=str(item_id)[:120],
        url=url,
        label=f'{product_slug.title()} — {item_type}',
    )
    # Phase 9 — Admiralty FOIA bridge: lift FOIA metadata onto the project
    # when the source is an Admiralty FOIA request and the project is still
    # a STANDARD kind. Idempotent: re-promoting the same FOIA item updates
    # existing foia_metadata fields with the latest values from the feed.
    if (
        product_slug == 'admiralty'
        and item_type == 'foia_request'
        and project.kind == Project.Kind.STANDARD
    ):
        meta = dict(project.foia_metadata or {})
        meta['foia_request_id'] = str(item_id)
        meta['admiralty_url'] = url
        if fleet_item:
            for key in (
                'received_at', 'statutory_deadline', 'agency',
                'requester_organization', 'requester_name',
            ):
                if fleet_item.get(key) is not None:
                    meta[f'foia_{key}' if not key.startswith('foia_') else key] = fleet_item[key]
        project.kind = Project.Kind.FOIA
        project.foia_metadata = meta
        # ADD-2: promote the time-sensitive metadata to first-class fields so
        # the statutory clock can be queried, indexed, and rendered as a
        # countdown badge.
        from datetime import date as _date
        from tasks.foia import recompute_deadline
        update_fields = ['kind', 'foia_metadata']
        received_str = (fleet_item or {}).get('received_at')
        if received_str:
            try:
                project.foia_received_at = _date.fromisoformat(received_str)
                update_fields.append('foia_received_at')
            except (TypeError, ValueError):
                pass
        # Jurisdiction: prefer explicit fleet_item value (Admiralty knows
        # which statute applies), then existing project value, then CT
        # default (DECD posture — most requests are CT, not federal).
        feed_jurisdiction = (fleet_item or {}).get('jurisdiction')
        valid = {c[0] for c in Project.FOIAJurisdiction.choices}
        if feed_jurisdiction in valid:
            project.foia_jurisdiction = feed_jurisdiction
            update_fields.append('foia_jurisdiction')
        elif not project.foia_jurisdiction:
            project.foia_jurisdiction = Project.FOIAJurisdiction.CONNECTICUT
            update_fields.append('foia_jurisdiction')
        project.save(update_fields=update_fields)
        # Compute the deadline if we have a received_at.
        if project.foia_received_at:
            recompute_deadline(project)
            # Auto-create the three default FOIA stage tasks. Only on first
            # promotion (idempotency: skip if any of the canonical titles
            # already exist on the project).
            existing_titles = set(
                project.tasks.values_list('title', flat=True)
            )
            default_tasks = [
                ('Acknowledge receipt within 5 business days', Task.Priority.HIGH),
                ('Search responsive records', Task.Priority.HIGH),
                ('Release / withhold by statutory deadline', Task.Priority.URGENT),
            ]
            for title, prio in default_tasks:
                if title not in existing_titles:
                    create_task(
                        project=project, title=title,
                        user=user, priority=prio,
                        description=(
                            f'Auto-created from Admiralty FOIA promotion. '
                            f'Statutory deadline: {project.foia_statutory_deadline_at}.'
                        ),
                    )
    log_audit(
        user=_u(user),
        action='task.promote',
        entity_type=task._meta.label,
        entity_id=str(task.pk),
        description=f'Promoted {product_slug}/{item_type}/{item_id} to task',
        changes={'product': product_slug, 'item_type': item_type},
        ip_address=_ip(user),
    )
    return task


@transaction.atomic
def add_task_collaborator(
    *, task: Task, user, target_user=None, email: str = '',
    role: str = TaskCollaborator.Role.CONTRIBUTOR,
) -> TaskCollaborator:
    """Add a TASK-scoped collaborator. Internal users auto-accept; external
    email-only invites stay pending until accepted (v2)."""
    if target_user is None and not email:
        raise ValueError('Must provide target_user or email.')
    defaults = {'role': role, 'invited_by': _u(user)}
    if target_user is not None:
        defaults['accepted_at'] = timezone.now()
        collab, created = TaskCollaborator.objects.get_or_create(
            task=task, user=target_user, defaults=defaults,
        )
    else:
        collab, created = TaskCollaborator.objects.get_or_create(
            task=task, email=email, defaults=defaults,
        )
    if created:
        log_audit(
            user=_u(user),
            action='task.collaborator.add',
            entity_type=task._meta.label,
            entity_id=str(task.pk),
            description=f'Added collaborator {target_user or email} as {role}',
            changes={'target': target_user.username if target_user else email, 'role': role},
            ip_address=_ip(user),
        )
    return collab


# Backward-compat alias (existing views still import the old name).
add_collaborator = add_task_collaborator


@transaction.atomic
def remove_task_collaborator(*, collaborator: TaskCollaborator, user) -> None:
    task = collaborator.task
    target = collaborator.user.username if collaborator.user else collaborator.email
    collaborator.delete()
    log_audit(
        user=_u(user),
        action='task.collaborator.remove',
        entity_type=task._meta.label,
        entity_id=str(task.pk),
        description=f'Removed collaborator {target}',
        changes={'target': target},
        ip_address=_ip(user),
    )


# Backward-compat alias.
remove_collaborator = remove_task_collaborator


def default_project(user) -> Project:
    """Return (or lazy-create) the catch-all inbox project used for promotions."""
    project, _ = Project.objects.get_or_create(
        slug='inbox',
        defaults={'name': 'Inbox', 'color': 'gray', 'created_by': _u(user)},
    )
    return project


# ---------------------------------------------------------------------------
# Project lifecycle services (NEW — Phase 3)
# ---------------------------------------------------------------------------
@transaction.atomic
def create_project(
    *, name: str, user, description: str = '', color: str = 'blue',
    kind: str = Project.Kind.STANDARD, started_at=None, target_end_at=None,
) -> Project:
    """Create a project. Slug is auto-generated; ``Project.save()`` handles
    collisions by appending a numeric suffix.
    """
    project = Project.objects.create(
        name=name,
        description=description,
        color=color,
        kind=kind,
        started_at=started_at,
        target_end_at=target_end_at,
        created_by=_u(user),
    )
    log_audit(
        user=_u(user),
        action='project.create',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=f'Created project "{name}"',
        changes={'name': name, 'kind': kind, 'slug': project.slug},
        ip_address=_ip(user),
    )
    return project


@transaction.atomic
def claim_project(
    *, project: Project, user, by_manager=None, notes: str = '',
) -> ProjectAssignment:
    """Claim a project. Mirrors Harbor's reassign-on-conflict semantics:

    - If the project is already claimed by ``user``, return the existing
      assignment (idempotent self-claim).
    - If claimed by someone else, mark the prior assignment ``REASSIGNED``
      and create a new one.
    - If unclaimed, create a fresh assignment.

    ``by_manager`` is the manager performing a manager-initiated assignment;
    when set, ``assignment_type`` is MANAGER_ASSIGNED instead of CLAIMED.
    """
    existing = ProjectAssignment.objects.filter(
        project=project,
        status=ProjectAssignment.Status.IN_PROGRESS,
    ).first()
    if existing and existing.assigned_to_id == user.id:
        return existing
    if existing:
        existing.status = ProjectAssignment.Status.REASSIGNED
        existing.released_at = timezone.now()
        existing.save(update_fields=['status', 'released_at'])
    assignment = ProjectAssignment.objects.create(
        project=project,
        assigned_to=user,
        assigned_by=by_manager,
        assignment_type=(
            ProjectAssignment.AssignmentType.MANAGER_ASSIGNED if by_manager
            else ProjectAssignment.AssignmentType.CLAIMED
        ),
        status=ProjectAssignment.Status.IN_PROGRESS,
        notes=notes,
    )
    log_audit(
        user=_u(user),
        action='project.claim',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=f'{user} claimed project {project.slug}',
        changes={'reassigned': bool(existing)},
        ip_address=_ip(user),
    )
    # On manager-initiated claim, notify the new lead (don't notify on
    # self-claim — the user just performed the action).
    if by_manager is not None:
        notify(
            event='helm_project_assigned',
            actor=_u(by_manager),
            context={'project': project, 'recipient': user, 'title': project.name},
        )
    return assignment


@transaction.atomic
def release_project(
    *, project: Project, user, notes: str = '',
) -> ProjectAssignment | None:
    """Release the user's active assignment on this project."""
    assignment = ProjectAssignment.objects.filter(
        project=project, assigned_to=user,
        status=ProjectAssignment.Status.IN_PROGRESS,
    ).first()
    if not assignment:
        return None
    assignment.status = ProjectAssignment.Status.RELEASED
    assignment.released_at = timezone.now()
    if notes:
        assignment.notes = (assignment.notes + '\n' + notes).strip() if assignment.notes else notes
    assignment.save(update_fields=['status', 'released_at', 'notes'])
    log_audit(
        user=_u(user),
        action='project.release',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=f'{user} released project {project.slug}',
        changes={'notes': notes} if notes else {},
        ip_address=_ip(user),
    )
    return assignment


@transaction.atomic
def add_project_collaborator(
    *, project: Project, user, target_user=None, email: str = '',
    role: str = ProjectCollaborator.Role.CONTRIBUTOR,
) -> ProjectCollaborator:
    """Add a PROJECT-scoped collaborator. Distinct from
    ``add_task_collaborator`` — project collaborators see the whole project.
    """
    if target_user is None and not email:
        raise ValueError('Must provide target_user or email.')
    defaults = {'role': role, 'invited_by': _u(user)}
    if target_user is not None:
        defaults['accepted_at'] = timezone.now()
        # Populate email from target_user so the (project, email) unique
        # constraint works correctly across both internal and external invites.
        defaults['email'] = target_user.email or ''
        collab, created = ProjectCollaborator.objects.get_or_create(
            project=project, user=target_user, defaults=defaults,
        )
    else:
        collab, created = ProjectCollaborator.objects.get_or_create(
            project=project, email=email, defaults=defaults,
        )
    # If the row exists but was previously deactivated, re-activate it.
    if not created and not collab.is_active:
        collab.is_active = True
        collab.role = role
        collab.save(update_fields=['is_active', 'role'])
    if created or not collab.is_active:
        log_audit(
            user=_u(user),
            action='project.collaborator.invite',
            entity_type=project._meta.label,
            entity_id=str(project.pk),
            description=f'Invited {target_user or email} as {role} to {project.slug}',
            changes={'target': target_user.username if target_user else email, 'role': role},
            ip_address=_ip(user),
        )
        if target_user is not None:
            # Internal user — standard in-app + email path.
            notify(
                event='helm_project_collaborator_invited',
                actor=_u(user),
                context={'project': project, 'collaborator': collab, 'title': project.name},
            )
        else:
            # External email — caller-supplied recipients (no User row).
            # Per "no magic links" policy, the email body just links to the
            # project; if they sign in via SSO they'll get access via the
            # collaborator row.
            from django.core.mail import send_mail
            from django.template.loader import render_to_string
            from django.conf import settings
            ctx = {
                'project': project, 'collaborator': collab,
                'title': project.name, 'invited_by': user,
                'site_url': getattr(settings, 'KEEL_PRODUCT_HOST', '') + project.get_absolute_url(),
            }
            try:
                body_html = render_to_string(
                    'notifications/emails/helm_project_collaborator_invited_external.html', ctx,
                )
                body_txt = render_to_string(
                    'notifications/emails/helm_project_collaborator_invited_external.txt', ctx,
                )
                send_mail(
                    subject=f'You were invited to a Helm project: {project.name}',
                    message=body_txt,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    html_message=body_html,
                    fail_silently=True,
                )
            except Exception:
                # Email dispatch is best-effort; don't fail the invite txn.
                pass
    return collab


@transaction.atomic
def remove_project_collaborator(*, collaborator: ProjectCollaborator, user) -> None:
    """Soft-deactivate (set ``is_active=False``) rather than hard-delete, so
    history queries can still surface who used to be on the project."""
    if not collaborator.is_active:
        return
    project = collaborator.project
    target = collaborator.user.username if collaborator.user else collaborator.email
    collaborator.is_active = False
    collaborator.save(update_fields=['is_active'])
    log_audit(
        user=_u(user),
        action='project.collaborator.remove',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=f'Removed collaborator {target} from {project.slug}',
        changes={'target': target},
        ip_address=_ip(user),
    )


@transaction.atomic
def add_project_note(
    *, project: Project, user, content: str, is_internal: bool = True,
) -> ProjectNote:
    note = ProjectNote.objects.create(
        project=project,
        author=_u(user),
        content=content,
        is_internal=is_internal,
    )
    log_audit(
        user=_u(user),
        action='project.note.add',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=f'Added note ({len(content)} chars) to {project.slug}',
        changes={'is_internal': is_internal, 'length': len(content)},
        ip_address=_ip(user),
    )
    notify(
        event='helm_project_note_added',
        actor=_u(user),
        context={'project': project, 'note': note, 'title': project.name},
    )
    return note


@transaction.atomic
def add_project_attachment(
    *, project: Project, user, file, description: str = '',
    visibility: str = ProjectAttachment.Visibility.INTERNAL,
) -> ProjectAttachment:
    attachment = ProjectAttachment.objects.create(
        project=project,
        uploaded_by=_u(user),
        file=file,
        description=description,
        visibility=visibility,
        source=ProjectAttachment.Source.UPLOAD,
    )
    log_audit(
        user=_u(user),
        action='project.attachment.upload',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=f'Uploaded {attachment.filename} to {project.slug}',
        changes={'filename': attachment.filename, 'visibility': visibility},
        ip_address=_ip(user),
    )
    notify(
        event='helm_project_attachment_added',
        actor=_u(user),
        context={'project': project, 'attachment': attachment, 'title': project.name},
    )
    return attachment


@transaction.atomic
def transition_project(
    *, project: Project, user, target_status: str, comment: str = '',
) -> Project:
    """Engine-validated project status transition.

    Side-effects:
    - Stamps ``completed_at`` on transition into COMPLETED.
    - Records a ``ProjectStatusHistory`` row via the workflow engine.

    Role enforcement runs through ``ProjectWorkflowEngine._user_has_role``
    (see ``tasks/workflow.py``) which resolves the ``'lead'`` keyword
    against active assignments + LEAD-role collaborators.
    """
    project.transition(target_status, user=user, comment=comment)
    if target_status == Project.Status.COMPLETED and not project.completed_at:
        project.completed_at = timezone.now()
        project.save(update_fields=['completed_at'])
    log_audit(
        user=_u(user),
        action='project.transition',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=f'Transitioned project {project.slug} to {target_status}',
        changes={'target_status': target_status, 'comment': comment},
        ip_address=_ip(user),
    )
    notify(
        event='helm_project_status_changed',
        actor=_u(user),
        context={'project': project, 'title': project.name, 'new_status': target_status},
    )
    return project


# Retention policy → years mapping (None = permanent, no expiry date).
_RETENTION_YEARS = {
    ArchivedProjectRecord.RetentionPolicy.STANDARD: 7,
    ArchivedProjectRecord.RetentionPolicy.EXTENDED: 10,
    ArchivedProjectRecord.RetentionPolicy.PERMANENT: None,
}


@transaction.atomic
def archive_project(
    *, project: Project, user, comment: str = '',
    retention: str = ArchivedProjectRecord.RetentionPolicy.STANDARD,
) -> Project:
    """Archive a project. Writes BOTH:

    1. The live row's ``status='archived'`` + ``archived_at=now`` (fast
       filtering for active/archived list views), and
    2. A new ``ArchivedProjectRecord`` row carrying retention policy and
       NARA-shaped metadata for the keel-shipped purge_expired_archives
       management command.

    Idempotent: a second call on an already-archived project returns the
    project unchanged.

    Stashes ``previous_terminal_status`` so ``unarchive_project`` can
    restore the prior state. The engine validates role permissions via
    ``ProjectWorkflowEngine``.
    """
    if project.is_archived:
        return project

    # Remember the pre-archive status for unarchive UX.
    project.previous_terminal_status = project.status

    # Engine validates the transition AND records ProjectStatusHistory.
    project.transition('archived', user=user, comment=comment or 'Archived')

    project.archived_at = timezone.now()
    project.save(update_fields=[
        'archived_at', 'status', 'previous_terminal_status', 'updated_at',
    ])

    # Retention row.
    years = _RETENTION_YEARS.get(retention, _RETENTION_YEARS[ArchivedProjectRecord.RetentionPolicy.STANDARD])
    expires_at = timezone.now() + timedelta(days=365 * years) if years else None
    ArchivedProjectRecord.objects.create(
        entity_type='project',
        entity_id=str(project.public_id),
        entity_description=project.name,
        retention_policy=retention,
        original_created_at=project.created_at,
        archived_by=_u(user),
        retention_expires_at=expires_at,
        metadata={
            'slug': project.slug,
            'kind': project.kind,
            'completed_at': project.completed_at.isoformat() if project.completed_at else None,
        },
    )

    log_audit(
        user=_u(user),
        action='archive',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=comment or f'Archived project {project.slug}',
        changes={'retention': retention, 'previous_terminal_status': project.previous_terminal_status},
        ip_address=_ip(user),
    )
    notify(
        event='helm_project_archived',
        actor=_u(user),
        context={'project': project, 'title': project.name},
    )
    return project


@transaction.atomic
def toll_foia(
    *, project: Project, user, tolled_at, tolled_until, comment: str = '',
) -> Project:
    """Pause the FOIA statutory clock between two dates. Recomputes the
    deadline by extending it by the tolled span (in business days).
    """
    from tasks.foia import recompute_deadline
    if project.kind != Project.Kind.FOIA:
        raise ValueError('Cannot toll a non-FOIA project.')
    project.foia_tolled_at = tolled_at
    project.foia_tolled_until = tolled_until
    project.save(update_fields=['foia_tolled_at', 'foia_tolled_until'])
    recompute_deadline(project)
    log_audit(
        user=_u(user),
        action='foia.toll',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=(
            f'FOIA tolling: {tolled_at.isoformat()} → {tolled_until.isoformat()}. '
            f'New deadline: {project.foia_statutory_deadline_at}.'
        ),
        changes={
            'tolled_at': tolled_at.isoformat(),
            'tolled_until': tolled_until.isoformat(),
            'reason': comment,
        },
        ip_address=_ip(user),
    )
    return project


@transaction.atomic
def untoll_foia(*, project: Project, user, comment: str = '') -> Project:
    """Clear the tolling and recompute the deadline back to its base value."""
    from tasks.foia import recompute_deadline
    if project.kind != Project.Kind.FOIA:
        raise ValueError('Cannot untoll a non-FOIA project.')
    project.foia_tolled_at = None
    project.foia_tolled_until = None
    project.save(update_fields=['foia_tolled_at', 'foia_tolled_until'])
    recompute_deadline(project)
    log_audit(
        user=_u(user),
        action='foia.untoll',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=f'FOIA tolling cleared. Deadline restored to {project.foia_statutory_deadline_at}.',
        changes={'reason': comment},
        ip_address=_ip(user),
    )
    return project


@transaction.atomic
def unarchive_project(*, project: Project, user, comment: str = '') -> Project:
    """Restore an archived project to its prior terminal status (or
    ``'active'`` as a fallback when ``previous_terminal_status`` is unset).

    Idempotent: a no-op on an already-unarchived project.
    """
    if not project.is_archived:
        return project

    target = project.previous_terminal_status or Project.Status.ACTIVE
    project.transition(target, user=user, comment=comment or 'Unarchived')
    project.archived_at = None
    project.previous_terminal_status = ''
    project.save(update_fields=[
        'archived_at', 'status', 'previous_terminal_status', 'updated_at',
    ])

    log_audit(
        user=_u(user),
        action='unarchive',
        entity_type=project._meta.label,
        entity_id=str(project.pk),
        description=comment or f'Unarchived project {project.slug} → {target}',
        changes={'restored_to': target},
        ip_address=_ip(user),
    )
    notify(
        event='helm_project_unarchived',
        actor=_u(user),
        context={'project': project, 'title': project.name},
    )
    return project
