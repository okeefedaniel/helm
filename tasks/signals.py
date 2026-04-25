"""Signal handlers for Helm Tasks lifecycle events.

Currently hosts only the post_save handler for TaskComment which fires
the ``helm_task_comment_added`` notification. Comments are created
directly via the TaskCommentForm in views.py rather than through a
service, so a signal is the cleanest hook.

Connected in ``TasksConfig.ready()``.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from keel.notifications import notify

from tasks.models import TaskComment


@receiver(post_save, sender=TaskComment, dispatch_uid='helm_tasks_comment_added')
def _on_comment_created(sender, instance, created, **kwargs):
    if not created:
        return
    notify(
        event='helm_task_comment_added',
        actor=instance.author,
        context={
            'task': instance.task,
            'project': instance.task.project,
            'comment': instance,
            'title': instance.task.title,
        },
    )
