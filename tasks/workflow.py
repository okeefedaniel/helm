"""Helm-local ``WorkflowEngine`` subclass that resolves the ``'lead'`` role.

Keel's base ``WorkflowEngine._user_has_role`` does not know about per-project
collaborators. Helm needs the LEAD of a project — either the active
``ProjectAssignment.assigned_to`` or anyone holding an active
``ProjectCollaborator(role=LEAD)`` row — to be able to transition that
project. We resolve this by overriding ``_user_has_role`` on a Helm-local
subclass that consults the bound ``obj`` (the ``Project`` instance) and the
relevant satellite tables.

See ``keel/CLAUDE.md`` §"Workflows & Status Tracking" → "Object-scoped
roles" for the contract on the ``obj=`` parameter.
"""
from keel.core.workflow import WorkflowEngine


class ProjectWorkflowEngine(WorkflowEngine):
    """Resolves the ``'lead'`` keyword against project-scoped collaborators."""

    def _user_has_role(self, user, required_roles, obj=None):
        # Defer to the base for the existing role keywords + system_admin +
        # superuser fast paths.
        if super()._user_has_role(user, required_roles, obj=obj):
            return True
        if 'lead' not in required_roles or obj is None:
            return False
        # Imports inside the method to avoid app-loading cycles.
        from tasks.models import ProjectAssignment, ProjectCollaborator
        if ProjectAssignment.objects.filter(
            project=obj,
            assigned_to=user,
            status=ProjectAssignment.Status.IN_PROGRESS,
        ).exists():
            return True
        return ProjectCollaborator.objects.filter(
            project=obj,
            user=user,
            role=ProjectCollaborator.Role.LEAD,
            is_active=True,
        ).exists()
