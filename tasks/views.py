from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import ProjectForm, PromoteForm, TaskCommentForm, TaskForm
from .models import Project, Task, TaskCollaborator, TaskComment
from .services import (
    add_collaborator,
    create_task,
    default_project,
    promote_fleet_item_to_task,
    remove_collaborator,
    reorder_task,
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
          .annotate(open_count=Count('tasks', filter=~Q(tasks__status=Task.Status.DONE)))
          .order_by('archived_at', 'name'))
    return render(request, 'tasks/project_list.html', {'projects': qs})


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
def project_detail(request, slug):
    project = get_object_or_404(Project, slug=slug)
    view_mode = request.GET.get('view', 'list')
    tasks_qs = (project.tasks
                .select_related('assignee', 'created_by')
                .order_by('position', '-created_at'))
    context = {
        'project': project,
        'view_mode': view_mode,
        'status_choices': Task.Status.choices,
        'priority_choices': Task.Priority.choices,
    }
    if view_mode == 'board':
        context['columns'] = _group_by_status(tasks_qs)
    else:
        context['tasks'] = tasks_qs
    return render(request, 'tasks/project_detail.html', context)


@login_required
def task_create(request, slug):
    project = get_object_or_404(Project, slug=slug)
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
def task_detail(request, pk):
    task = get_object_or_404(Task.objects.select_related('project', 'assignee', 'created_by'), pk=pk)
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
def task_edit(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if request.method == 'POST':
        form = TaskForm(request.POST, instance=task)
        if form.is_valid():
            update_task(task, user=request.user, **form.cleaned_data)
            return redirect(task.get_absolute_url())
    else:
        form = TaskForm(instance=task)
    return render(request, 'tasks/task_form.html', {'form': form, 'project': task.project, 'task': task})


@login_required
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not (request.user.is_staff or request.user == task.created_by):
        return HttpResponse(status=403)
    project_url = task.project.get_absolute_url()
    task.delete()
    messages.success(request, 'Task deleted.')
    return redirect(project_url)


@login_required
@require_POST
def task_status(request, pk):
    """HTMX endpoint: inline status change from list/board/detail views."""
    task = get_object_or_404(Task, pk=pk)
    new_status = request.POST.get('status')
    if new_status not in dict(Task.Status.choices):
        return HttpResponseBadRequest('invalid status')
    update_task(task, user=request.user, status=new_status)
    return render(request, 'tasks/partials/task_row.html', {'task': task})


@login_required
@require_POST
def task_reorder(request, pk):
    """HTMX endpoint called by Sortable.js drag-drop on the board."""
    task = get_object_or_404(Task, pk=pk)
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
@require_POST
def collaborator_add(request, pk):
    task = get_object_or_404(Task, pk=pk)
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
