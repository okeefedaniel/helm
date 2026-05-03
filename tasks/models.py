"""Helm Project Management — government-first PM models.

Follows the DockLabs Project Lifecycle Standard (keel/CLAUDE.md §279-430).
Concrete subclasses of keel abstracts; no intermediate "project" base layer
(the suite uses concrete-with-FK, not subclassed-base).

Model graph:

    Project (WorkflowModelMixin + ArchivableMixin)
       ├─ ProjectAssignment        (AbstractAssignment) — claim row
       ├─ ProjectCollaborator      (AbstractCollaborator) — invited users
       ├─ ProjectAttachment        (AbstractAttachment) — uploaded files
       ├─ ProjectNote              (AbstractInternalNote) — diligence notes
       ├─ ProjectStatusHistory     (AbstractStatusHistory) — transition log
       └─ Task (WorkflowModelMixin)
              ├─ TaskCollaborator  (AbstractCollaborator) — task-scoped
              ├─ TaskComment       (concrete) — task chat
              ├─ TaskLink          (concrete) — soft cross-product refs
              └─ TaskStatusHistory (AbstractStatusHistory)

    ArchivedProjectRecord (AbstractArchivedRecord) — NARA-shaped retention

PK choice: ``Project`` and ``Task`` keep ``BigAutoField`` (existing data,
URL stability) and gain ``public_id`` UUID alongside for cross-product
references. Satellite models inherit ``KeelBaseModel``'s UUID PK natively.

The legacy ``Project.archived`` boolean is retained in DB through Deploy A
but removed from the model definition; reads route through
``ArchivableMixin.is_archived`` which checks ``archived_at``. A follow-up
migration drops the column in Deploy B.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from keel.core.archive import ArchivableMixin, ArchiveQuerySetMixin
from keel.core.models import (
    AbstractArchivedRecord,
    AbstractAssignment,
    AbstractAttachment,
    AbstractCollaborator,
    AbstractInternalNote,
    AbstractStatusHistory,
    WorkflowModelMixin,
)


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


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------
class ProjectQuerySet(ArchiveQuerySetMixin, models.QuerySet):
    """Custom queryset for Project, with archive helpers and per-user ACL."""

    def with_open_count(self):
        return self.annotate(
            open_count=models.Count(
                'tasks',
                filter=~models.Q(tasks__status=Task.Status.DONE),
                distinct=True,
            ),
        )

    def visible_to(self, user):
        """Return only the projects this user may view.

        Visibility rules (any one grants access):
        - Anonymous → no projects.
        - Superuser / is_staff / role='system_admin' → every project.
        - Active ``ProjectAssignment(assigned_to=user)`` → that project.
        - Active ``ProjectCollaborator(user=user, is_active=True)`` → that project.
        - ``Project.created_by=user`` → that project.

        ``helm_admin`` / ``agency_admin`` are the baseline customer-side
        roles every Helm user holds — they grant product access, not
        cross-project visibility. Only ``system_admin`` (suite-wide tier)
        bypasses per-project ACL.

        Distinct() guards against the JOIN duplication when a user is both
        creator and collaborator.
        """
        if user is None or not getattr(user, 'is_authenticated', False):
            return self.none()
        role = getattr(user, 'role', '') or ''
        if (
            getattr(user, 'is_superuser', False)
            or getattr(user, 'is_staff', False)
            or role == 'system_admin'
        ):
            return self
        return self.filter(
            models.Q(created_by=user)
            | models.Q(
                assignments__assigned_to=user,
                assignments__status='in_progress',
            )
            | models.Q(
                collaborators__user=user,
                collaborators__is_active=True,
            )
        ).distinct()


class Project(WorkflowModelMixin, ArchivableMixin, models.Model):
    """Government-first PM container with full lifecycle compliance."""

    # Project kind enum. ADD-2 added FOIA (statutory clock); ADD-1 adds CIP
    # (capital improvement plan with federal fund-source modeling).
    class Kind(models.TextChoices):
        STANDARD = 'standard', 'Standard'
        FOIA = 'foia', 'FOIA Request'
        CIP = 'cip', 'Capital Improvement Plan'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ON_HOLD = 'on_hold', 'On hold'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
        ARCHIVED = 'archived', 'Archived'

    # ``id`` (BigAutoField) inherited from Django default — URL stability.
    public_id = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True,
    )

    slug = models.SlugField(max_length=140, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=16, choices=COLOR_CHOICES, default='blue')

    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.STANDARD, db_index=True,
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True,
    )

    # Pre-archive status, restored on unarchive.
    previous_terminal_status = models.CharField(
        max_length=16, choices=Status.choices, blank=True, default='',
        help_text='Status the project was in before archive. Restored on unarchive.',
    )

    # FOIA / cross-product metadata bridge — populated when an Admiralty
    # FOIA request is promoted into a Helm project (Phase 9).
    foia_metadata = models.JSONField(default=dict, blank=True)

    # ADD-2 — FOIA statutory clock. Promoted from foia_metadata to
    # first-class fields so the clock can be queried, indexed, and
    # rendered as a countdown badge. Only meaningful when kind=FOIA.
    class FOIAJurisdiction(models.TextChoices):
        CONNECTICUT = 'connecticut', 'Connecticut (CGS §1-206 — 4 business days to acknowledge)'
        FEDERAL = 'federal', 'Federal (5 USC 552 — 20 business days)'
        # Other state jurisdictions can be added as follow-on:
        # CALIFORNIA = 'california', 'California PRA (10 calendar days)'
        # TEXAS = 'texas', 'Texas Public Information Act (10 business days)'

    foia_jurisdiction = models.CharField(
        max_length=24, choices=FOIAJurisdiction.choices,
        default=FOIAJurisdiction.CONNECTICUT, blank=True,
        help_text='Jurisdiction whose statutory deadline applies. Connecticut default (DECD posture).',
    )
    foia_received_at = models.DateField(
        null=True, blank=True,
        help_text='Date the FOIA request was received. Triggers the clock.',
    )
    foia_statutory_deadline_at = models.DateField(
        null=True, blank=True, db_index=True,
        help_text='Computed statutory deadline. Recomputed when received_at, '
                  'jurisdiction, or tolling changes.',
    )
    foia_tolled_at = models.DateField(
        null=True, blank=True,
        help_text='Date the clock was paused (tolled).',
    )
    foia_tolled_until = models.DateField(
        null=True, blank=True,
        help_text='Date the tolling ends and the clock resumes.',
    )

    # ADD-1 — CIP project type. Federal fund sources tracked as a structured
    # JSON list: [{"source": "arpa", "amount_cents": 240000000, "label": "..."}].
    # Boolean federal-eligibility flags drive an audit-ready compliance posture.
    # Only meaningful when kind=CIP.
    class FundSource(models.TextChoices):
        ARPA = 'arpa', 'ARPA — American Rescue Plan Act'
        IIJA = 'iija', 'IIJA — Infrastructure Investment and Jobs Act'
        IRA = 'ira', 'IRA — Inflation Reduction Act'
        BEAD = 'bead', 'BEAD — Broadband Equity, Access, and Deployment'
        SLCGP = 'slcgp', 'SLCGP — State and Local Cybersecurity Grant'
        CDBG = 'cdbg', 'CDBG — Community Development Block Grant'
        GO_BOND = 'go_bond', 'General Obligation Bond'
        REVENUE_BOND = 'revenue_bond', 'Revenue Bond'
        STATE_MATCH = 'state_match', 'State Match'
        LOCAL_MATCH = 'local_match', 'Local Match'
        GENERAL_FUND = 'general_fund', 'General Fund'

    fund_sources = models.JSONField(
        default=list, blank=True,
        help_text='List of {source, amount_cents, label} dicts. Source is from FundSource.',
    )

    # Federal compliance flags — only relevant for federal-fund-tied CIP projects.
    requires_davis_bacon = models.BooleanField(
        default=False,
        help_text='Davis-Bacon Act prevailing wage requirements apply (federal construction).',
    )
    requires_baba = models.BooleanField(
        default=False,
        help_text='Build America, Buy America domestic-procurement requirements apply.',
    )
    requires_nepa = models.BooleanField(
        default=False,
        help_text='NEPA environmental review required (federal funds + ground disturbance).',
    )
    requires_environmental_review = models.BooleanField(
        default=False,
        help_text='State or local environmental review required.',
    )

    # ADD-3 — Public transparency. When PUBLIC, the project is visible at
    # /p/<public_id>/ to anyone (no auth) — name, status, target dates,
    # task completion %, fund sources (CIP only). NO notes, NO attachments,
    # NO collaborators, NO PII. Default PRIVATE. LEAD-only toggle.
    class PublicVisibility(models.TextChoices):
        PRIVATE = 'private', 'Private'
        PUBLIC = 'public', 'Public'

    public_visibility = models.CharField(
        max_length=10,
        choices=PublicVisibility.choices,
        default=PublicVisibility.PRIVATE,
        db_index=True,
        help_text='Public projects render at /p/<public_id>/ to unauthenticated visitors.',
    )

    started_at = models.DateField(null=True, blank=True)
    target_end_at = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # archived_at provided by ArchivableMixin.

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='helm_projects_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProjectQuerySet.as_manager()

    class Meta:
        ordering = ['archived_at', 'name']
        indexes = [
            models.Index(fields=['status', 'archived_at']),
            models.Index(fields=['kind', 'status']),
        ]

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
    def WORKFLOW(self):
        from tasks.workflows import PROJECT_WORKFLOW
        return PROJECT_WORKFLOW

    @property
    def open_task_count(self):
        return self.tasks.exclude(status=Task.Status.DONE).count()


class ProjectAssignment(AbstractAssignment):
    """Claim of a project by its principal driver (LEAD)."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='assignments')


class ProjectCollaborator(AbstractCollaborator):
    """Project-level invites — full project visibility for the invitee.

    Coexists with ``TaskCollaborator``: a user invited to a project sees the
    whole thing; a user invited only to a task sees only that task.
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='collaborators')

    class Meta(AbstractCollaborator.Meta):
        unique_together = [('project', 'user'), ('project', 'email')]


class ProjectAttachment(AbstractAttachment):
    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='attachments')


class ProjectNote(AbstractInternalNote):
    """Project-level diligence notes / discussion."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='notes')


class ProjectStatusHistory(AbstractStatusHistory):
    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='status_history')


class ArchivedProjectRecord(AbstractArchivedRecord):
    """NARA-shaped retention record. Pairs with ``Project.archived_at``:
    the live row carries the fast-filter flag, this row carries the
    retention policy and (later) NARA disposition class metadata.
    """
    pass


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
class Task(WorkflowModelMixin, models.Model):
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

    public_id = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False,
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.TODO, db_index=True,
    )
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.MEDIUM, db_index=True,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='helm_tasks_assigned',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='helm_tasks_created',
    )
    due_date = models.DateField(null=True, blank=True)
    position = models.PositiveIntegerField(default=0, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_overdue_notif_at = models.DateTimeField(null=True, blank=True)
    last_due_soon_notif_at = models.DateTimeField(null=True, blank=True)
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

    @property
    def WORKFLOW(self):
        from tasks.workflows import TASK_WORKFLOW
        return TASK_WORKFLOW

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


class TaskStatusHistory(AbstractStatusHistory):
    task = models.ForeignKey(Task, on_delete=models.CASCADE,
                             related_name='status_history')


class TaskComment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class TaskLink(models.Model):
    """Soft cross-product reference — no FK, just strings + URL.

    ``item_id`` accepts either a legacy integer pk or a UUID string
    (``public_id``). Going forward, peer-product feeds expose ``public_id``
    so cross-DB references are stable.
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
    """Task-scoped invites — coexists with ``ProjectCollaborator``.

    A user invited to a project gets project-wide visibility; a user invited
    only to a task sees only that task. Both flows ship in v1.
    """
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='collaborators')

    class Meta(AbstractCollaborator.Meta):
        unique_together = [('task', 'user'), ('task', 'email')]
        ordering = ['-invited_at']
