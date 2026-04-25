from django.contrib import admin

from .models import (
    ArchivedProjectRecord, Project, ProjectAssignment, ProjectAttachment,
    ProjectCollaborator, ProjectNote, ProjectStatusHistory,
    Task, TaskCollaborator, TaskComment, TaskLink, TaskStatusHistory,
)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'kind', 'status', 'color', 'archived_at', 'created_at')
    list_filter = ('kind', 'status', 'color')
    search_fields = ('name', 'slug')
    readonly_fields = ('public_id', 'archived_at', 'previous_terminal_status')


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


# ---------------------------------------------------------------------------
# Phase 12 — satellite table admin registrations
# ---------------------------------------------------------------------------
@admin.register(ProjectAssignment)
class ProjectAssignmentAdmin(admin.ModelAdmin):
    list_display = ('project', 'assigned_to', 'assignment_type', 'status', 'claimed_at')
    list_filter = ('assignment_type', 'status')
    search_fields = ('project__slug', 'assigned_to__username', 'assigned_to__email')
    raw_id_fields = ('project', 'assigned_to', 'assigned_by')


@admin.register(ProjectCollaborator)
class ProjectCollaboratorAdmin(admin.ModelAdmin):
    list_display = (
        'project', 'user', 'email', 'role', 'is_active', 'invited_at',
    )
    list_filter = ('role', 'is_active')
    search_fields = ('project__slug', 'user__username', 'email')
    raw_id_fields = ('project', 'user', 'invited_by')


@admin.register(ProjectAttachment)
class ProjectAttachmentAdmin(admin.ModelAdmin):
    list_display = (
        'project', 'filename', 'visibility', 'source', 'uploaded_by', 'uploaded_at',
    )
    list_filter = ('visibility', 'source')
    search_fields = ('project__slug', 'filename')
    raw_id_fields = ('project', 'uploaded_by')


@admin.register(ProjectNote)
class ProjectNoteAdmin(admin.ModelAdmin):
    list_display = ('project', 'author', 'is_internal', 'created_at')
    list_filter = ('is_internal',)
    search_fields = ('project__slug', 'content')
    raw_id_fields = ('project', 'author')


@admin.register(ProjectStatusHistory)
class ProjectStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ('project', 'old_status', 'new_status', 'changed_by', 'changed_at')
    list_filter = ('new_status',)
    raw_id_fields = ('project', 'changed_by')

    def has_add_permission(self, request):
        return False  # immutable — only the WorkflowEngine writes these

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TaskStatusHistory)
class TaskStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ('task', 'old_status', 'new_status', 'changed_by', 'changed_at')
    list_filter = ('new_status',)
    raw_id_fields = ('task', 'changed_by')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ArchivedProjectRecord)
class ArchivedProjectRecordAdmin(admin.ModelAdmin):
    list_display = (
        'entity_id', 'entity_description', 'retention_policy',
        'archived_at', 'retention_expires_at', 'is_purged',
    )
    list_filter = ('retention_policy', 'is_purged')
    search_fields = ('entity_id', 'entity_description')
    readonly_fields = (
        'entity_type', 'entity_id', 'entity_description', 'retention_policy',
        'original_created_at', 'archived_at', 'archived_by',
        'retention_expires_at', 'metadata',
    )
