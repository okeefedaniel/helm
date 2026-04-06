"""Rename app_label from 'core' to 'helm_core' for suite shared DB."""

from django.db import migrations

OLD_LABEL = 'core'
NEW_LABEL = 'helm_core'


def forwards(apps, schema_editor):
    conn = schema_editor.connection
    # Helm already uses helm_* table names — no table renames needed.
    # Just update Django internal records.
    schema_editor.execute(
        "UPDATE django_content_type SET app_label = %s WHERE app_label = %s",
        [NEW_LABEL, OLD_LABEL],
    )
    schema_editor.execute(
        "UPDATE django_migrations SET app = %s WHERE app = %s",
        [NEW_LABEL, OLD_LABEL],
    )


def backwards(apps, schema_editor):
    schema_editor.execute(
        "UPDATE django_content_type SET app_label = %s WHERE app_label = %s",
        [OLD_LABEL, NEW_LABEL],
    )
    schema_editor.execute(
        "UPDATE django_migrations SET app = %s WHERE app = %s",
        [OLD_LABEL, NEW_LABEL],
    )


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('helm_core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
