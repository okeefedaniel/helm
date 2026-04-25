from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from keel.core.archive import ArchiveListView

from keel.core.audit import log_audit

from .access import project_access_required, task_access_required, workflow_view
from . import exports
from .forms import (
    ProjectAttachmentForm, ProjectCollaboratorForm, ProjectForm, ProjectNoteForm,
    ProjectTransitionForm, PromoteForm, TaskCommentForm, TaskForm,
)
from .models import (
    Project, ProjectCollaborator, Task, TaskCollaborator, TaskComment,
)
from .services import (
    add_collaborator,
    add_project_attachment,
    add_project_collaborator,
    add_project_note,
    archive_project,
    claim_project,
    create_task,
    default_project,
    promote_fleet_item_to_task,
    release_project,
    remove_collaborator,
    remove_project_collaborator,
    reorder_task,
    transition_project,
    transition_task,
    unarchive_project,
    update_task,
)

STATUS_ORDER = [
    Task.Status.TODO,
    Task.Status.IN_PROGRESS,
    Task.Status.BLOCKED,
    Task.Status.DONE,
]


def _group_by_status(qs):
    buckets = {s: [] for s in STATUS_ORDER}
    for t in qs:
        buckets.setdefault(t.status, []).append(t)
    return [(s, Task.Status(s).label, buckets.get(s, [])) for s in STATUS_ORDER]


@login_required
def my_tasks(request):
    # "Mine" = tasks I'm assigned OR collaborating on.
    qs = (Task.objects
          .filter(Q(assignee=request.user) | Q(collaborators__user=request.user))
          .distinct()
          .select_related('project', 'assignee'))
    groups = _group_by_status(qs)
    return render(request, 'tasks/my_tasks.html', {
        'groups': groups,
        'open_count': qs.exclude(status=Task.Status.DONE).count(),
    })


@login_required
def project_list(request):
    qs = (Project.objects
          .visible_to(request.user)
          .active()  # hide archived; "View archive" link in template
          .annotate(open_count=Count('tasks', filter=~Q(tasks__status=Task.Status.DONE)))
          .order_by('name'))
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'tasks/project_list.html', {
        'projects': page_obj.object_list,
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
    })


@login_required
def project_create(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            project.save()
            messages.success(request, f'Project "{project.name}" created.')
            return redirect(project.get_absolute_url())
    else:
        form = ProjectForm()
    return render(request, 'tasks/project_form.html', {'form': form})


@login_required
@project_access_required
def project_detail(request, slug):
    project = request.project
    view_mode = request.GET.get('view', 'list')
    tasks_qs = (project.tasks
                .select_related('assignee', 'created_by')
                .order_by('position', '-created_at'))
    # Active assignment (the LEAD). None when project is unclaimed.
    active_assignment = (project.assignments
                         .filter(status='in_progress')
                         .select_related('assigned_to')
                         .first())
    available_transitions = project.WORKFLOW.get_available_transitions(
        project.status, user=request.user, obj=project,
    )
    context = {
        'project': project,
        'view_mode': view_mode,
        'status_choices': Task.Status.choices,
        'priority_choices': Task.Priority.choices,
        'active_assignment': active_assignment,
        'available_transitions': available_transitions,
        'collaborators': project.collaborators.filter(is_active=True)
                                              .select_related('user'),
    }
    if view_mode == 'board':
        context['columns'] = _group_by_status(tasks_qs)
    else:
        context['tasks'] = tasks_qs
    return render(request, 'tasks/project_detail.html', context)


@login_required
@project_access_required
def task_create(request, slug):
    project = request.project
    if request.method == 'POST':
        form = TaskForm(request.POST)
        if form.is_valid():
            task = create_task(
                project=project,
                title=form.cleaned_data['title'],
                description=form.cleaned_data.get('description', ''),
                status=form.cleaned_data['status'],
                priority=form.cleaned_data['priority'],
                assignee=form.cleaned_data.get('assignee'),
                due_date=form.cleaned_data.get('due_date'),
                user=request.user,
            )
            return redirect(task.get_absolute_url())
    else:
        form = TaskForm(initial={'status': Task.Status.TODO, 'priority': Task.Priority.MEDIUM})
    return render(request, 'tasks/task_form.html', {'form': form, 'project': project})


@login_required
@task_access_required
def task_detail(request, pk):
    task = request.task
    comment_form = TaskCommentForm()
    if request.method == 'POST':
        comment_form = TaskCommentForm(request.POST)
        if comment_form.is_valid():
            comment = comment_form.save(commit=False)
            comment.task = task
            comment.author = request.user
            comment.save()
            return redirect(task.get_absolute_url())
    from django.contrib.auth import get_user_model
    User = get_user_model()
    invitable = (User.objects
                 .exclude(pk=task.assignee_id) if task.assignee_id else User.objects.all())
    invitable = invitable.exclude(pk__in=task.collaborators.values_list('user_id', flat=True))
    return render(request, 'tasks/task_detail.html', {
        'task': task,
        'comments': task.comments.select_related('author'),
        'links': task.links.all(),
        'collaborators': task.collaborators.select_related('user', 'invited_by'),
        'invitable_users': invitable.order_by('username')[:200],
        'collab_roles': TaskCollaborator.Role.choices,
        'comment_form': comment_form,
        'status_choices': Task.Status.choices,
        'priority_choices': Task.Priority.choices,
    })


@login_required
@task_access_required
def task_edit(request, pk):
    task = request.task
    if request.method == 'POST':
        form = TaskForm(request.POST, instance=task)
        if form.is_valid():
            update_task(task, user=request.user, **form.cleaned_data)
            return redirect(task.get_absolute_url())
    else:
        form = TaskForm(instance=task)
    return render(request, 'tasks/task_form.html', {'form': form, 'project': task.project, 'task': task})


@login_required
@task_access_required
@require_POST
def task_delete(request, pk):
    task = request.task
    if not (request.user.is_staff or request.user == task.created_by):
        return HttpResponse(status=403)
    project_url = task.project.get_absolute_url()
    task.delete()
    messages.success(request, 'Task deleted.')
    return redirect(project_url)


@login_required
@task_access_required
@workflow_view
@require_POST
def task_status(request, pk):
    """HTMX endpoint: inline status change from list/board/detail views."""
    task = request.task
    new_status = request.POST.get('status')
    if new_status not in dict(Task.Status.choices):
        return HttpResponseBadRequest('invalid status')
    update_task(task, user=request.user, status=new_status)
    return render(request, 'tasks/partials/task_row.html', {'task': task})


@login_required
@task_access_required
@workflow_view
@require_POST
def task_reorder(request, pk):
    """HTMX endpoint called by Sortable.js drag-drop on the board."""
    task = request.task
    new_status = request.POST.get('status')
    try:
        new_position = int(request.POST.get('position', 0))
    except (TypeError, ValueError):
        return HttpResponseBadRequest('invalid position')
    if new_status not in dict(Task.Status.choices):
        return HttpResponseBadRequest('invalid status')
    reorder_task(task, user=request.user, new_status=new_status, new_position=new_position)
    return JsonResponse({'ok': True, 'id': task.pk, 'status': task.status})


@login_required
def inbox(request):
    qs = (Task.objects.filter(assignee__isnull=True)
          .exclude(status=Task.Status.DONE)
          .select_related('project'))
    return render(request, 'tasks/inbox.html', {'tasks': qs})


@login_required
def promote(request):
    """Create a task from a fleet item. Called by the promote-button partial."""
    if request.method != 'POST':
        # Show a form populated from the GET params so the user can confirm.
        initial = {
            'title': request.GET.get('title', ''),
            'product_slug': request.GET.get('product_slug', ''),
            'item_type': request.GET.get('item_type', ''),
            'item_id': request.GET.get('item_id', ''),
            'url': request.GET.get('url', ''),
            'priority': request.GET.get('priority', Task.Priority.MEDIUM),
            'project': default_project(request.user).pk,
        }
        form = PromoteForm(initial=initial)
        return render(request, 'tasks/promote_form.html', {'form': form})

    form = PromoteForm(request.POST)
    if not form.is_valid():
        return render(request, 'tasks/promote_form.html', {'form': form}, status=400)
    cd = form.cleaned_data
    task = promote_fleet_item_to_task(
        project=cd['project'],
        user=request.user,
        title=cd['title'],
        description=cd.get('description', ''),
        priority=cd['priority'],
        product_slug=cd['product_slug'],
        item_type=cd['item_type'],
        item_id=cd.get('item_id', ''),
        url=cd['url'],
    )
    messages.success(request, f'Promoted to task: {task.title}')
    return redirect(task.get_absolute_url())


@login_required
@task_access_required
@require_POST
def collaborator_add(request, pk):
    task = request.task
    role = request.POST.get('role', TaskCollaborator.Role.CONTRIBUTOR)
    user_id = request.POST.get('user_id')
    email = request.POST.get('email', '').strip()
    target_user = None
    if user_id:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        target_user = get_object_or_404(User, pk=user_id)
    elif not email:
        return HttpResponseBadRequest('Provide user_id or email.')
    add_collaborator(task=task, user=request.user, target_user=target_user, email=email, role=role)
    messages.success(request, 'Collaborator added.')
    return redirect(task.get_absolute_url())


@login_required
@task_access_required
@require_POST
def collaborator_remove(request, pk, collab_id):
    collab = get_object_or_404(TaskCollaborator, pk=collab_id, task_id=pk)
    if not (request.user.is_staff
            or request.user == collab.invited_by
            or request.user == collab.task.created_by
            or request.user == collab.user):
        return HttpResponse(status=403)
    task_url = collab.task.get_absolute_url()
    remove_collaborator(collaborator=collab, user=request.user)
    return redirect(task_url)


@login_required
def my_tasks_widget(request):
    """HTMX partial — renders the dashboard widget body."""
    qs = (Task.objects
          .filter(Q(assignee=request.user) | Q(collaborators__user=request.user))
          .exclude(status=Task.Status.DONE)
          .distinct()
          .select_related('project')
          .order_by('due_date', '-priority')[:6])
    return render(request, 'tasks/partials/my_tasks_widget.html', {'tasks': qs})


# ---------------------------------------------------------------------------
# Phase 5 — project lifecycle views
# ---------------------------------------------------------------------------
@login_required
@project_access_required
@workflow_view
@require_POST
def claim_project_view(request, slug):
    notes = request.POST.get('notes', '')
    claim_project(project=request.project, user=request.user, notes=notes)
    messages.success(request, f'You claimed "{request.project.name}".')
    return redirect(request.project.get_absolute_url())


@login_required
@project_access_required
@workflow_view
@require_POST
def release_project_view(request, slug):
    notes = request.POST.get('notes', '')
    result = release_project(project=request.project, user=request.user, notes=notes)
    if result is None:
        messages.warning(request, 'You did not have an active claim to release.')
    else:
        messages.success(request, f'Released your claim on "{request.project.name}".')
    return redirect(request.project.get_absolute_url())


@login_required
@project_access_required
@workflow_view
@require_POST
def project_transition_view(request, slug):
    form = ProjectTransitionForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('invalid form')
    transition_project(
        project=request.project, user=request.user,
        target_status=form.cleaned_data['status'],
        comment=form.cleaned_data['comment'],
    )
    messages.success(request, f'Project moved to {form.cleaned_data["status"]}.')
    return redirect(request.project.get_absolute_url())


@login_required
@project_access_required
@workflow_view
@require_POST
def archive_project_view(request, slug):
    archive_project(
        project=request.project, user=request.user,
        comment=request.POST.get('comment', ''),
        retention=request.POST.get('retention', 'standard'),
    )
    messages.success(request, f'Archived "{request.project.name}".')
    return redirect('tasks:archived_projects')


@login_required
@project_access_required
@workflow_view
@require_POST
def unarchive_project_view(request, slug):
    unarchive_project(
        project=request.project, user=request.user,
        comment=request.POST.get('comment', ''),
    )
    messages.success(request, f'Unarchived "{request.project.name}".')
    return redirect(request.project.get_absolute_url())


@login_required
@project_access_required
@workflow_view
def project_collaborators_view(request, slug):
    project = request.project
    if request.method == 'POST':
        form = ProjectCollaboratorForm(request.POST)
        if form.is_valid():
            target_user = None
            if form.cleaned_data.get('user_id'):
                from django.contrib.auth import get_user_model
                User = get_user_model()
                target_user = get_object_or_404(User, pk=form.cleaned_data['user_id'])
            add_project_collaborator(
                project=project, user=request.user,
                target_user=target_user,
                email=form.cleaned_data.get('email', ''),
                role=form.cleaned_data['role'],
            )
            messages.success(request, 'Collaborator invited.')
            return redirect('tasks:project_collaborators', slug=slug)
    else:
        form = ProjectCollaboratorForm()
    from django.contrib.auth import get_user_model
    User = get_user_model()
    invited_user_ids = project.collaborators.filter(
        is_active=True, user__isnull=False,
    ).values_list('user_id', flat=True)
    invitable = User.objects.exclude(pk__in=invited_user_ids).order_by('username')[:200]
    return render(request, 'tasks/project_collaborators.html', {
        'project': project,
        'form': form,
        'collaborators': project.collaborators.filter(is_active=True)
                                              .select_related('user', 'invited_by'),
        'invitable_users': invitable,
        'roles': ProjectCollaborator.Role.choices,
    })


@login_required
@project_access_required
@workflow_view
@require_POST
def project_collaborator_remove_view(request, slug, collab_id):
    collab = get_object_or_404(
        ProjectCollaborator, pk=collab_id, project=request.project,
    )
    remove_project_collaborator(collaborator=collab, user=request.user)
    messages.success(request, 'Collaborator removed.')
    return redirect('tasks:project_collaborators', slug=slug)


@login_required
@project_access_required
@workflow_view
def project_notes_view(request, slug):
    project = request.project
    if request.method == 'POST':
        form = ProjectNoteForm(request.POST)
        if form.is_valid():
            add_project_note(
                project=project, user=request.user,
                content=form.cleaned_data['content'],
                is_internal=form.cleaned_data.get('is_internal', True),
            )
            return redirect('tasks:project_notes', slug=slug)
    else:
        form = ProjectNoteForm()
    return render(request, 'tasks/project_notes.html', {
        'project': project,
        'form': form,
        'notes': project.notes.select_related('author').order_by('-created_at'),
    })


@login_required
@project_access_required
@workflow_view
def project_attachments_view(request, slug):
    project = request.project
    if request.method == 'POST':
        form = ProjectAttachmentForm(request.POST, request.FILES)
        if form.is_valid():
            add_project_attachment(
                project=project, user=request.user,
                file=form.cleaned_data['file'],
                description=form.cleaned_data.get('description', ''),
                visibility=form.cleaned_data['visibility'],
            )
            return redirect('tasks:project_attachments', slug=slug)
    else:
        form = ProjectAttachmentForm()
    return render(request, 'tasks/project_attachments.html', {
        'project': project,
        'form': form,
        'attachments': project.attachments.select_related('uploaded_by')
                                          .order_by('-uploaded_at'),
    })


# ---------------------------------------------------------------------------
# Phase 5 — task transition (engine-validated, supersedes task_status)
# ---------------------------------------------------------------------------
@login_required
@task_access_required
@workflow_view
@require_POST
def task_transition_view(request, pk):
    """Engine-validated task status transition.

    Supersedes the legacy ``task_status`` HTMX endpoint (which uses the
    older ``update_task`` service that bypasses the engine and history
    recording). New code should call this endpoint; ``task_status``
    remains as a back-compat alias.
    """
    new_status = request.POST.get('status')
    if new_status not in dict(Task.Status.choices):
        return HttpResponseBadRequest('invalid status')
    transition_task(
        task=request.task, user=request.user,
        target_status=new_status,
        comment=request.POST.get('comment', ''),
    )
    return render(request, 'tasks/partials/task_row.html', {'task': request.task})


# ---------------------------------------------------------------------------
# Phase 5 — archived projects list (uses keel.core.archive.ArchiveListView)
# ---------------------------------------------------------------------------
class ArchivedProjectsView(LoginRequiredMixin, ArchiveListView):
    """Per-user archived projects list. Filters through visible_to() so a
    user only sees archives of projects they had access to."""
    model = Project
    template_name = 'tasks/archived_projects.html'
    archive_label = 'Projects'

    def get_queryset(self):
        # Restrict to projects the user can access. visible_to() applied
        # before the archived filter (parent class adds archived_at__isnull=False).
        visible_ids = (Project.objects.visible_to(self.request.user)
                                       .values_list('id', flat=True))
        return (super().get_queryset()
                .filter(id__in=visible_ids))


archived_projects = ArchivedProjectsView.as_view()


# ---------------------------------------------------------------------------
# Phase 7 — CSV / PDF export
# ---------------------------------------------------------------------------
@login_required
@project_access_required
def export_project_csv(request, slug):
    """Stream a project's task list as CSV."""
    response = exports.project_to_csv(request.project)
    log_audit(
        user=request.user, action='export',
        entity_type='helm_tasks.Project', entity_id=str(request.project.pk),
        description=f'CSV export of {request.project.slug} tasks',
        ip_address=getattr(request.user, 'audit_ip', None),
    )
    return response


@login_required
@project_access_required
def export_project_pdf(request, slug):
    """Render a project status report as PDF (ReportLab)."""
    response = exports.project_to_pdf(request.project)
    log_audit(
        user=request.user, action='export',
        entity_type='helm_tasks.Project', entity_id=str(request.project.pk),
        description=f'PDF status report for {request.project.slug}',
        ip_address=getattr(request.user, 'audit_ip', None),
    )
    return response
