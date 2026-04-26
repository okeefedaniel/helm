"""Notification type registrations for Helm Project Management.

Wires 12 notification events into the keel.notifications registry. Service
functions in tasks/services.py call ``keel.notifications.notify(event=...)``
when the corresponding lifecycle action runs; this module declares the
recipient resolution, channel defaults, and email templates for each.

All keys are prefixed ``helm_`` to namespace them inside the suite-wide
registry (the registry warns on duplicate keys per
keel/notifications/registry.py:72).

Email templates are only required for events whose ``default_channels``
include ``'email'``; for in-app-only events, the keel notify() pipeline
skips template rendering entirely.

The ``recipient_resolver`` callables operate on the ``context`` dict
passed to ``notify()`` — typical context is::

    {'project': project, 'task': task, 'note': note, 'collaborator': c, ...}
"""
from __future__ import annotations

from keel.notifications import NotificationType, register


# ---------------------------------------------------------------------------
# Recipient resolvers
# ---------------------------------------------------------------------------
def _project_lead(context):
    """The active LEAD on the project (assignment with status=in_progress)."""
    project = context.get('project')
    if project is None:
        return []
    a = project.assignments.filter(status='in_progress').select_related('assigned_to').first()
    return [a.assigned_to] if a and a.assigned_to_id else []


def _project_followers(context, *, only_status=False, only_notes=False):
    """LEAD + active project collaborators, optionally filtered by per-collab
    notify_on_status / notify_on_notes opt-outs.
    """
    project = context.get('project')
    if project is None:
        return []
    users = []
    a = project.assignments.filter(status='in_progress').select_related('assigned_to').first()
    if a and a.assigned_to_id:
        users.append(a.assigned_to)
    qs = project.collaborators.filter(is_active=True, user__isnull=False).select_related('user')
    if only_status:
        qs = qs.filter(notify_on_status=True)
    if only_notes:
        qs = qs.filter(notify_on_notes=True)
    users.extend(c.user for c in qs)
    # De-dup while preserving order.
    seen = set()
    out = []
    for u in users:
        if u.pk not in seen:
            seen.add(u.pk)
            out.append(u)
    return out


def _project_followers_status(context):
    return _project_followers(context, only_status=True)


def _project_followers_notes(context):
    return _project_followers(context, only_notes=True)


def _new_assignee(context):
    """The user newly assigned to the project (passed via context as 'recipient')."""
    r = context.get('recipient')
    return [r] if r else []


def _new_collaborator_user(context):
    """The internal user just invited to the project."""
    c = context.get('collaborator')
    return [c.user] if c and c.user_id else []


def _task_assignee(context):
    task = context.get('task')
    return [task.assignee] if task and task.assignee_id else []


def _task_collaborators(context, *, only_status=False, only_notes=False):
    """Active task-scoped collaborators, plus the task assignee."""
    task = context.get('task')
    if task is None:
        return []
    users = []
    if task.assignee_id:
        users.append(task.assignee)
    qs = task.collaborators.filter(is_active=True, user__isnull=False).select_related('user')
    if only_status:
        qs = qs.filter(notify_on_status=True)
    if only_notes:
        qs = qs.filter(notify_on_notes=True)
    users.extend(c.user for c in qs)
    seen = set()
    out = []
    for u in users:
        if u.pk not in seen:
            seen.add(u.pk)
            out.append(u)
    return out


def _task_collaborators_status(context):
    return _task_collaborators(context, only_status=True)


def _task_collaborators_notes(context):
    return _task_collaborators(context, only_notes=True)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
HELM_PM_CATEGORY = 'Work Management'


def register_all():
    """Register every Helm PM notification type. Called from
    ``TasksConfig.ready()``.

    Idempotent — keel's registry warns on duplicate keys but does not raise,
    so re-registration during test reload is safe.
    """
    # ── Project lifecycle ──────────────────────────────────────────────
    register(NotificationType(
        key='helm_project_assigned',
        label='Project Assigned to You',
        description='You have been assigned as the lead on a project.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app', 'email'],
        priority='medium',
        email_template='notifications/emails/helm_project_assigned.html',
        email_subject='You are now leading: {title}',
        recipient_resolver=_new_assignee,
        link_template='/tasks/projects/{project.slug}/',
    ))

    register(NotificationType(
        key='helm_project_collaborator_invited',
        label='Project Invitation',
        description='You have been invited to collaborate on a project.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app', 'email'],
        priority='medium',
        email_template='notifications/emails/helm_project_collaborator_invited.html',
        email_subject='You were invited to: {title}',
        recipient_resolver=_new_collaborator_user,
        link_template='/tasks/projects/{project.slug}/',
    ))

    register(NotificationType(
        key='helm_project_collaborator_invited_external',
        label='Project Invitation (External)',
        description='An external collaborator has been invited to a project.',
        category=HELM_PM_CATEGORY,
        # External invites have no User row, so in_app cannot deliver — email
        # only. The dispatcher handles this by skipping channels for which
        # the recipient lacks the underlying mechanism.
        default_channels=['email'],
        priority='medium',
        email_template='notifications/emails/helm_project_collaborator_invited_external.html',
        email_subject='You were invited to a Helm project',
        # No recipient_resolver: caller passes recipients=[stub_user] or
        # uses the ad-hoc email branch via send_email directly. See
        # services.add_project_collaborator.
        link_template='/tasks/projects/{project.slug}/',
    ))

    register(NotificationType(
        key='helm_project_status_changed',
        label='Project Status Changed',
        description='A project you follow changed status.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app'],
        priority='low',
        recipient_resolver=_project_followers_status,
        link_template='/tasks/projects/{project.slug}/',
    ))

    register(NotificationType(
        key='helm_project_archived',
        label='Project Archived',
        description='A project you follow was archived.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app'],
        priority='low',
        recipient_resolver=_project_followers,
        link_template='/tasks/projects/{project.slug}/',
    ))

    register(NotificationType(
        key='helm_project_unarchived',
        label='Project Unarchived',
        description='A project you follow was unarchived.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app'],
        priority='low',
        recipient_resolver=_project_followers,
        link_template='/tasks/projects/{project.slug}/',
    ))

    register(NotificationType(
        key='helm_project_note_added',
        label='New Project Note',
        description='A diligence note was added to a project you follow.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app'],
        priority='low',
        recipient_resolver=_project_followers_notes,
        link_template='/tasks/projects/{project.slug}/notes/',
    ))

    register(NotificationType(
        key='helm_project_attachment_added',
        label='New Project Attachment',
        description='A file was attached to a project you follow.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app'],
        priority='low',
        recipient_resolver=_project_followers_notes,
        link_template='/tasks/projects/{project.slug}/attachments/',
    ))

    # ── Task lifecycle ─────────────────────────────────────────────────
    register(NotificationType(
        key='helm_task_assigned',
        label='Task Assigned to You',
        description='You have been assigned to a task.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app', 'email'],
        priority='medium',
        email_template='notifications/emails/helm_task_assigned.html',
        email_subject='New task: {title}',
        recipient_resolver=_task_assignee,
        link_template='/tasks/t/{task.pk}/',
    ))

    register(NotificationType(
        key='helm_task_status_changed',
        label='Task Status Changed',
        description='A task you follow changed status.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app'],
        priority='low',
        recipient_resolver=_task_collaborators_status,
        link_template='/tasks/t/{task.pk}/',
    ))

    register(NotificationType(
        key='helm_task_comment_added',
        label='New Task Comment',
        description='Someone commented on a task you follow.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app'],
        priority='low',
        recipient_resolver=_task_collaborators_notes,
        link_template='/tasks/t/{task.pk}/',
    ))

    register(NotificationType(
        key='helm_task_due_soon',
        label='Task Due Soon',
        description='A task assigned to you is due within 24 hours.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app', 'email'],
        priority='medium',
        email_template='notifications/emails/helm_task_due_soon.html',
        email_subject='Task due soon: {title}',
        recipient_resolver=_task_assignee,
        link_template='/tasks/t/{task.pk}/',
    ))

    register(NotificationType(
        key='helm_task_overdue',
        label='Task Overdue',
        description='A task assigned to you is past its due date.',
        category=HELM_PM_CATEGORY,
        default_channels=['in_app', 'email'],
        priority='high',
        email_template='notifications/emails/helm_task_overdue.html',
        email_subject='Task overdue: {title}',
        recipient_resolver=_task_assignee,
        link_template='/tasks/t/{task.pk}/',
    ))
