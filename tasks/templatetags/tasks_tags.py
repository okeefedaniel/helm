from django import template
from django.conf import settings
from django.urls import reverse

register = template.Library()


STATUS_COLOR = {
    'todo': 'slate',
    'in_progress': 'blue',
    'blocked': 'red',
    'done': 'green',
}

PRIORITY_COLOR = {
    'low': 'slate',
    'medium': 'blue',
    'high': 'brass',
    'urgent': 'red',
}


@register.simple_tag
def tasks_enabled():
    return getattr(settings, 'HELM_TASKS_ENABLED', False)


@register.filter
def status_pill(status):
    return STATUS_COLOR.get(status, 'slate')


@register.filter
def priority_pill(priority):
    return PRIORITY_COLOR.get(priority, 'slate')


@register.inclusion_tag('tasks/partials/promote_button.html')
def promote_button(item, product_slug=None):
    """Render a 'Promote to Task' button for an action-queue item or alert.

    `item` may be a dict (from the feed contract) or an object with attribute
    access. `product_slug` lets callers override when the item lacks one.
    """
    if not getattr(settings, 'HELM_TASKS_ENABLED', False):
        return {'enabled': False}

    def _get(key, default=''):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    slug = product_slug or _get('product') or _get('product_slug') or ''
    item_type = _get('type') or _get('item_type') or 'item'
    title = _get('title') or 'Fleet item'
    url = _get('deep_link') or _get('url') or ''
    item_id = _get('id') or _get('item_id') or ''
    priority = _get('priority') or 'medium'

    params = {
        'title': title,
        'product_slug': slug,
        'item_type': item_type,
        'item_id': str(item_id),
        'url': url,
        'priority': priority,
    }
    from urllib.parse import urlencode
    return {
        'enabled': True,
        'promote_url': reverse('tasks:promote') + '?' + urlencode(params),
    }


@register.inclusion_tag('tasks/partials/my_tasks_widget_shell.html', takes_context=True)
def my_tasks_widget(context):
    """Dashboard widget host — HTMX loads the body from tasks:partial_my_tasks."""
    if not getattr(settings, 'HELM_TASKS_ENABLED', False):
        return {'enabled': False}
    return {
        'enabled': True,
        'partial_url': reverse('tasks:partial_my_tasks'),
        'tasks_url': reverse('tasks:my_tasks'),
    }
