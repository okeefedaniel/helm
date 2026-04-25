"""Rename app_label from 'core' to 'helm_core' for suite shared DB."""

from django.db import migrations

OLD_LABEL = 'core'
NEW_LABEL = 'helm_core'


def forwards(apps, schema_editor):
    # Helm already uses helm_* table names — no table renames needed.
    # Just update Django internal records.
    #
    # Hardened 2026-04-25: previous form did blind UPDATEs which crashed on a
    # unique-constraint conflict if NEW_LABEL rows already existed (and silently
    # left orphan OLD_LABEL rows in the table on Postgres versions without the
    # constraint). Now we delete OLD_LABEL rows whose (NEW_LABEL, name) pair is
    # already present, then UPDATE the rest. Runs cleanly whether the rename has
    # never run, partially run, or fully run.
    schema_editor.execute(
        """
        DELETE FROM django_content_type
         WHERE app_label = %s
           AND model IN (
               SELECT model FROM django_content_type WHERE app_label = %s
           )
        """,
        [OLD_LABEL, NEW_LABEL],
    )
    schema_editor.execute(
        "UPDATE django_content_type SET app_label = %s WHERE app_label = %s",
        [NEW_LABEL, OLD_LABEL],
    )
    schema_editor.execute(
        """
        DELETE FROM django_migrations
         WHERE app = %s
           AND name IN (
               SELECT name FROM django_migrations WHERE app = %s
           )
        """,
        [OLD_LABEL, NEW_LABEL],
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
