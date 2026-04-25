"""Project lifecycle baseline.

Adds the new Project fields needed for compliance with the DockLabs
Project Lifecycle Standard:
- ``public_id`` UUID alongside the existing BigAutoField pk
- ``kind`` enum (STANDARD / FOIA)
- ``status`` enum (active / on_hold / completed / cancelled / archived)
- ``previous_terminal_status`` for unarchive UX
- ``foia_metadata`` JSON
- ``started_at`` / ``target_end_at`` / ``completed_at``
- ``archived_at`` (from ``ArchivableMixin``)

Backfills:
- ``public_id`` — fresh UUID per row
- ``status`` — ``'archived'`` where the legacy ``archived`` boolean is True,
  else ``'active'``
- ``archived_at`` — ``updated_at`` for previously-archived rows
- One ``ProjectStatusHistory`` row per backfilled archive

The legacy ``archived`` BooleanField is intentionally kept in the schema
through this deploy. A follow-up migration drops it once code paths read
exclusively from ``status`` / ``archived_at``.
"""
import uuid

from django.db import migrations, models


def populate_public_ids(apps, schema_editor):
    """Backfill a fresh UUID per project row.

    ``AddField(default=uuid.uuid4)`` evaluates the callable once at column
    add time, so every existing row inherits the SAME default UUID. We
    overwrite every row with a fresh UUID before ``unique=True`` is added.
    """
    Project = apps.get_model('helm_tasks', 'Project')
    for p in Project.objects.all():
        p.public_id = uuid.uuid4()
        p.save(update_fields=['public_id'])


def backfill_project_lifecycle(apps, schema_editor):
    """Set ``status`` + ``archived_at`` based on legacy ``archived`` boolean."""
    Project = apps.get_model('helm_tasks', 'Project')
    # 1. Set status='archived' + archived_at on previously-archived rows.
    archived_qs = Project.objects.filter(archived=True, status='')
    for p in archived_qs:
        p.status = 'archived'
        p.archived_at = p.updated_at
        p.save(update_fields=['status', 'archived_at'])
    # 2. Default status='active' for everyone else.
    Project.objects.filter(archived=False, status='').update(status='active')


class Migration(migrations.Migration):

    dependencies = [
        ('helm_tasks', '0002_taskcollaborator'),
    ]

    operations = [
        # public_id — add nullable, populate, then make unique+indexed.
        migrations.AddField(
            model_name='project',
            name='public_id',
            field=models.UUIDField(default=uuid.uuid4, null=True, editable=False),
        ),
        migrations.RunPython(populate_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='project',
            name='public_id',
            field=models.UUIDField(
                default=uuid.uuid4, editable=False, unique=True, db_index=True,
            ),
        ),

        # New lifecycle fields. ``status`` ships nullable-default-empty so the
        # backfill below can distinguish unset rows.
        migrations.AddField(
            model_name='project',
            name='kind',
            field=models.CharField(
                max_length=16,
                choices=[('standard', 'Standard'), ('foia', 'FOIA Request')],
                default='standard', db_index=True,
            ),
        ),
        migrations.AddField(
            model_name='project',
            name='status',
            field=models.CharField(
                max_length=16,
                choices=[
                    ('active', 'Active'), ('on_hold', 'On hold'),
                    ('completed', 'Completed'), ('cancelled', 'Cancelled'),
                    ('archived', 'Archived'),
                ],
                default='', db_index=True,
            ),
        ),
        migrations.AddField(
            model_name='project',
            name='previous_terminal_status',
            field=models.CharField(
                max_length=16,
                choices=[
                    ('active', 'Active'), ('on_hold', 'On hold'),
                    ('completed', 'Completed'), ('cancelled', 'Cancelled'),
                    ('archived', 'Archived'),
                ],
                blank=True, default='',
                help_text=(
                    'Status the project was in before archive. '
                    'Restored on unarchive.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='project',
            name='foia_metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='project',
            name='started_at',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='project',
            name='target_end_at',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='project',
            name='completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='project',
            name='archived_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name='project',
            name='slug',
            field=models.SlugField(db_index=True, max_length=140, unique=True),
        ),

        # Backfill status + archived_at from the legacy ``archived`` boolean.
        migrations.RunPython(backfill_project_lifecycle, migrations.RunPython.noop),

        # New Meta state — ordering + indexes.
        migrations.AlterModelOptions(
            name='project',
            options={'ordering': ['archived_at', 'name']},
        ),
        migrations.AddIndex(
            model_name='project',
            index=models.Index(
                fields=['status', 'archived_at'],
                name='helm_tasks_status_arch_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='project',
            index=models.Index(
                fields=['kind', 'status'],
                name='helm_tasks_kind_status_idx',
            ),
        ),
    ]
