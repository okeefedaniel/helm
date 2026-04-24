"""Business logic for Helm Tasks.

Keep views thin; do all mutations + audit logging here.
"""
from __future__ import annotations

from django.db import transaction

from .models import Project, Task, TaskCollaborator, TaskLink


def _audit(user, action, target, metadata=None):
    """Best-effort audit log entry. No-op if keel audit not wired."""
    try:
        from core.models import AuditLog
    except Exception:  # pragma: no cover
        return
    try:
        AuditLog.objects.create(
            user=user if (user and user.is_authenticated) else None,
            action=action,
            target_type=target.__class__.__name__,
            target_id=str(getattr(target, 'pk', '')),
            metadata=metadata or {},
        )
    except Exception:  # pragma: no cover
        # AuditLog field shape may differ across keel versions. Never let
        # logging failures break a task mutation.
        pass


@transaction.atomic
def create_task(*, project: Project, title: str, user, **fields) -> Task:
    position = (project.tasks.aggregate_max_position()
                if hasattr(project.tasks, 'aggregate_max_position')
                else (project.tasks.order_by('-position').values_list('position', flat=True).first() or 0) + 1)
    task = Task.objects.create(
        project=project,
        title=title,
        created_by=user if user and user.is_authenticated else None,
        position=position,
        **fields,
    )
    _audit(user, 'task.create', task, {'title': title, 'project': project.slug})
    return task


@transaction.atomic
def update_task(task: Task, *, user, **fields) -> Task:
    changed = {}
    for key, value in fields.items():
        if getattr(task, key, None) != value:
            changed[key] = value
            setattr(task, key, value)
    if 'status' in changed and changed['status'] == Task.Status.DONE and not task.completed_at:
        from django.utils import timezone
        task.completed_at = timezone.now()
        changed['completed_at'] = task.completed_at
    elif 'status' in changed and changed['status'] != Task.Status.DONE:
        task.completed_at = None
        changed['completed_at'] = None
    if changed:
        task.save()
        _audit(user, 'task.update', task, {'changed': list(changed.keys())})
    return task


@transaction.atomic
def reorder_task(task: Task, *, user, new_status: str, new_position: int) -> Task:
    task.status = new_status
    task.position = new_position
    if new_status == Task.Status.DONE and not task.completed_at:
        from django.utils import timezone
        task.completed_at = timezone.now()
    elif new_status != Task.Status.DONE:
        task.completed_at = None
    task.save(update_fields=['status', 'position', 'completed_at', 'updated_at'])
    _audit(user, 'task.reorder', task, {'status': new_status, 'position': new_position})
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
) -> Task:
    """Create a task from a fleet action-queue item or alert."""
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
    _audit(user, 'task.promote', task, {'product': product_slug, 'item_type': item_type})
    return task


@transaction.atomic
def add_collaborator(*, task: Task, user, target_user=None, email='', role=TaskCollaborator.Role.CONTRIBUTOR) -> TaskCollaborator:
    """Add a collaborator to a task.

    For internal users (`target_user` set), the collaborator is immediately
    active — they already have product access. For external `email`-only
    invites, the row is created in pending state; v2 will email them via the
    existing keel.accounts.Invitation flow.
    """
    if target_user is None and not email:
        raise ValueError('Must provide target_user or email.')
    defaults = {
        'role': role,
        'invited_by': user if user and user.is_authenticated else None,
    }
    if target_user is not None:
        from django.utils import timezone as _tz
        defaults['accepted_at'] = _tz.now()  # internal users auto-accept
        collab, created = TaskCollaborator.objects.get_or_create(
            task=task, user=target_user, defaults=defaults,
        )
    else:
        collab, created = TaskCollaborator.objects.get_or_create(
            task=task, email=email, defaults=defaults,
        )
    if created:
        _audit(user, 'task.collaborator.add', task,
               {'target': target_user.username if target_user else email, 'role': role})
    return collab


@transaction.atomic
def remove_collaborator(*, collaborator: TaskCollaborator, user) -> None:
    task = collaborator.task
    collaborator.delete()
    _audit(user, 'task.collaborator.remove', task,
           {'target': collaborator.user.username if collaborator.user else collaborator.email})


def default_project(user) -> Project:
    """Return (or lazy-create) the catch-all inbox project used for promotions."""
    project, _ = Project.objects.get_or_create(
        slug='inbox',
        defaults={'name': 'Inbox', 'color': 'gray', 'created_by': user if user and user.is_authenticated else None},
    )
    return project
