"""Per-project access control + service-layer error mapping.

Three decorators and one helper compose the Phase 4 security model:

- ``project_access_required`` — wraps slug-routed project views. Resolves
  the project, checks ``Project.objects.visible_to(request.user)``, raises
  ``Http404`` (NOT 403) on miss so the URL space doesn't leak project
  slugs to unauthorized users. The resolved project is attached to
  ``request.project`` so the view body uses ``request.project`` directly
  instead of re-fetching.

- ``task_access_required`` — wraps pk-routed task views. Resolves the
  task, then checks visibility on its parent project. Same 404 semantics.
  Attaches ``request.task`` and ``request.project``.

- ``workflow_view`` — maps service-layer exceptions to user-facing HTTP
  responses, with HTMX-aware payload shapes:
    ValidationError    → 400 (HTMX: JSON ``{"errors": [...]}``;
                                non-HTMX: redirect-with-message).
    PermissionDenied   → 403 (HTMX: JSON; non-HTMX: redirect-with-message).
    IntegrityError     → 409 (HTMX: JSON; non-HTMX: redirect-with-message).
  Without this wrapper, services that raise these would 500 to the user.

- ``_can_access(user, project)`` — boolean helper, used internally.

Why ``Http404`` not ``PermissionDenied`` on miss: information leak. A 403
on a slug an unauthorized user does not control would confirm the slug
exists. A 404 — identical to a slug that genuinely doesn't exist —
preserves URL-space privacy. NIST 800-171 §3.1.1.
"""
from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect

from .models import Project, Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _can_access(user, project) -> bool:
    """Return True if ``user`` may access ``project``."""
    return Project.objects.filter(pk=project.pk).visible_to(user).exists()


def _is_htmx(request) -> bool:
    return request.headers.get('HX-Request') == 'true'


# ---------------------------------------------------------------------------
# Project access decorator
# ---------------------------------------------------------------------------
def project_access_required(view_func):
    """Resolve ``slug`` → ``Project``, check visibility, attach to request.

    Raises Http404 on miss (404 not 403 — don't leak slug existence).
    """
    @wraps(view_func)
    def _wrapped(request, slug, *args, **kwargs):
        project = get_object_or_404(Project, slug=slug)
        if not _can_access(request.user, project):
            raise Http404
        request.project = project
        return view_func(request, slug, *args, **kwargs)
    return _wrapped


# ---------------------------------------------------------------------------
# Task access decorator
# ---------------------------------------------------------------------------
def task_access_required(view_func):
    """Resolve ``pk`` → ``Task``, check visibility on its project, attach
    both ``request.task`` and ``request.project`` to the request.
    """
    @wraps(view_func)
    def _wrapped(request, pk, *args, **kwargs):
        task = get_object_or_404(
            Task.objects.select_related('project'), pk=pk,
        )
        if not _can_access(request.user, task.project):
            raise Http404
        request.task = task
        request.project = task.project
        return view_func(request, pk, *args, **kwargs)
    return _wrapped


# ---------------------------------------------------------------------------
# Service-layer error mapping
# ---------------------------------------------------------------------------
def workflow_view(view_func):
    """Map service-layer exceptions to user-facing HTTP responses.

    Without this wrapper, the workflow engine's PermissionDenied from a
    non-LEAD user trying to transition would surface as a Django 500.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except ValidationError as e:
            msgs = list(e.messages) if hasattr(e, 'messages') else [str(e)]
            if _is_htmx(request):
                return JsonResponse({'errors': msgs}, status=400)
            messages.error(request, '; '.join(msgs))
            return redirect(request.META.get('HTTP_REFERER', '/'))
        except PermissionDenied as e:
            msg = str(e) or 'You do not have permission to perform this action.'
            if _is_htmx(request):
                return JsonResponse({'error': msg}, status=403)
            messages.error(request, msg)
            return redirect(request.META.get('HTTP_REFERER', '/'))
        except IntegrityError:
            if _is_htmx(request):
                return JsonResponse(
                    {'error': 'That action conflicts with existing data.'},
                    status=409,
                )
            messages.error(request, 'That action conflicts with existing data.')
            return redirect(request.META.get('HTTP_REFERER', '/'))
    return _wrapped
