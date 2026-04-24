"""Helm Tasks — optional task management suite.

Flat, fast schema inspired by Linear + monday.com + Asana. All models are
scoped to the Helm deployment; no cross-product foreign keys (peer references
use string slugs + URLs via TaskLink). See the plan file for rationale.
"""
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from keel.core.models import AbstractCollaborator


class Project(models.Model):
    COLOR_CHOICES = [
        ('blue', 'Blue'),
        ('teal', 'Teal'),
        ('green', 'Green'),
        ('yellow', 'Yellow'),
        ('orange', 'Orange'),
        ('red', 'Red'),
        ('purple', 'Purple'),
        ('gray', 'Gray'),
    ]

    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=16, choices=COLOR_CHOICES, default='blue')
    archived = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='helm_projects_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['archived', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or 'project'
            slug = base
            n = 2
            while Project.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('tasks:project_detail', args=[self.slug])

    @property
    def open_task_count(self):
        return self.tasks.exclude(status=Task.Status.DONE).count()


class Task(models.Model):
    class Status(models.TextChoices):
        TODO = 'todo', 'To Do'
        IN_PROGRESS = 'in_progress', 'In Progress'
        BLOCKED = 'blocked', 'Blocked'
        DONE = 'done', 'Done'

    class Priority(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'
        URGENT = 'urgent', 'Urgent'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TODO, db_index=True)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='helm_tasks_assigned',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='helm_tasks_created',
    )
    due_date = models.DateField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position', '-created_at']
        indexes = [
            models.Index(fields=['project', 'status']),
            models.Index(fields=['assignee', 'status']),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('tasks:task_detail', args=[self.pk])

    def mark_done(self, save=True):
        self.status = self.Status.DONE
        self.completed_at = timezone.now()
        if save:
            self.save(update_fields=['status', 'completed_at', 'updated_at'])

    @property
    def is_overdue(self):
        return bool(
            self.due_date
            and self.status != self.Status.DONE
            and self.due_date < timezone.localdate()
        )


class TaskComment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class TaskLink(models.Model):
    """Soft cross-product reference — no FK, just strings + URL.

    Created when a fleet item is promoted to a task, or when a user pastes
    a peer-product URL into a task.
    """
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='links')
    product_slug = models.CharField(max_length=32, blank=True)
    item_type = models.CharField(max_length=48, blank=True)
    item_id = models.CharField(max_length=120, blank=True)
    url = models.URLField(max_length=500)
    label = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return self.label or self.url


class TaskCollaborator(AbstractCollaborator):
    """A user invited to collaborate on a specific task.

    Uses the suite-wide ``AbstractCollaborator`` pattern (lead / contributor /
    reviewer / observer roles) so the vocabulary matches Harbor, Bounty, etc.
    Internal users are linked via ``user`` FK; external invites set ``email``
    and leave ``user`` null until they accept (deferred to v2 — for now we
    only support inviting existing Helm users).
    """
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='collaborators')

    class Meta(AbstractCollaborator.Meta):
        unique_together = [('task', 'user'), ('task', 'email')]
        ordering = ['-invited_at']
