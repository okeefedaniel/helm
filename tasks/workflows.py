"""Workflow transition tables for Helm projects and tasks.

Two engines:

- ``PROJECT_WORKFLOW`` — uses ``ProjectWorkflowEngine`` so the ``'lead'``
  role keyword resolves against per-project collaborator rows.
  Active ⇄ on_hold; active/on_hold → completed/cancelled; completed/cancelled
  → archived (terminal). Archived → active (unarchive); plus archived →
  completed and archived → cancelled so ``unarchive_project`` can restore
  the project to its prior terminal status when one was recorded.

- ``TASK_WORKFLOW`` — base ``WorkflowEngine`` with a simple todo →
  in_progress → done flow plus blocked and reopen.

Both engines auto-record an ``AbstractStatusHistory`` row on every
transition (``history_model`` + ``history_fk_field`` configured below).
"""
from keel.core.workflow import Transition, WorkflowEngine

from tasks.workflow import ProjectWorkflowEngine


PROJECT_WORKFLOW = ProjectWorkflowEngine(
    transitions=[
        Transition('active', 'on_hold', roles=['lead', 'system_admin'],
                   require_comment=True, label='Pause'),
        Transition('on_hold', 'active', roles=['lead', 'system_admin'],
                   label='Resume'),

        # Completion paths
        Transition('active', 'completed', roles=['lead', 'system_admin'],
                   label='Complete'),
        Transition('on_hold', 'completed', roles=['lead', 'system_admin'],
                   label='Complete'),

        # Cancellation paths
        Transition('active', 'cancelled', roles=['lead', 'system_admin'],
                   require_comment=True, label='Cancel'),
        Transition('on_hold', 'cancelled', roles=['lead', 'system_admin'],
                   require_comment=True, label='Cancel'),

        # Archive (terminal, per keel.core.archive convention)
        Transition('completed', 'archived', roles=['lead', 'system_admin'],
                   label='Archive'),
        Transition('cancelled', 'archived', roles=['lead', 'system_admin'],
                   label='Archive'),

        # Unarchive — services.unarchive_project picks the right target
        # based on Project.previous_terminal_status. We register all three
        # at the engine level; the service chooses.
        Transition('archived', 'active', roles=['lead', 'system_admin'],
                   label='Unarchive'),
        Transition('archived', 'completed', roles=['lead', 'system_admin'],
                   label='Unarchive (to completed)'),
        Transition('archived', 'cancelled', roles=['lead', 'system_admin'],
                   label='Unarchive (to cancelled)'),
    ],
    history_model='helm_tasks.ProjectStatusHistory',
    history_fk_field='project',
)


TASK_WORKFLOW = WorkflowEngine(
    transitions=[
        Transition('todo', 'in_progress', roles=['any'], label='Start'),
        Transition('in_progress', 'blocked', roles=['any'],
                   require_comment=True, label='Block'),
        Transition('blocked', 'in_progress', roles=['any'], label='Unblock'),
        Transition('in_progress', 'done', roles=['any'], label='Complete'),
        Transition('todo', 'done', roles=['any'], label='Skip to done'),
        Transition('done', 'in_progress', roles=['any'], label='Reopen'),
    ],
    history_model='helm_tasks.TaskStatusHistory',
    history_fk_field='task',
)
