from django.contrib import admin

from .models import Project, Task, TaskCollaborator, TaskComment, TaskLink


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'color', 'archived', 'created_at')
    list_filter = ('archived', 'color')
    search_fields = ('name', 'slug')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'status', 'priority', 'assignee', 'due_date', 'updated_at')
    list_filter = ('status', 'priority', 'project')
    search_fields = ('title', 'description')
    raw_id_fields = ('assignee', 'created_by', 'project')


@admin.register(TaskComment)
class TaskCommentAdmin(admin.ModelAdmin):
    list_display = ('task', 'author', 'created_at')


@admin.register(TaskLink)
class TaskLinkAdmin(admin.ModelAdmin):
    list_display = ('task', 'product_slug', 'item_type', 'item_id', 'url')
    search_fields = ('product_slug', 'item_type', 'item_id', 'url')


@admin.register(TaskCollaborator)
class TaskCollaboratorAdmin(admin.ModelAdmin):
    list_display = ('task', 'user', 'email', 'role', 'invited_by', 'invited_at', 'accepted_at')
    list_filter = ('role',)
    search_fields = ('email', 'user__username')
