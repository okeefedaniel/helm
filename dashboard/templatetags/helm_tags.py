"""Template tags for the Helm dashboard."""
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def currency(value):
    """Format a number as USD currency."""
    try:
        val = float(value)
        if val >= 1_000_000:
            return f'${val / 1_000_000:,.1f}M'
        if val >= 1_000:
            return f'${val / 1_000:,.0f}K'
        return f'${val:,.0f}'
    except (ValueError, TypeError):
        return value


@register.filter
def severity_class(severity):
    """Map severity to Bootstrap color class."""
    mapping = {
        'critical': 'danger',
        'warning': 'warning',
        'info': 'info',
        'normal': 'secondary',
    }
    return mapping.get(severity, 'secondary')


@register.filter
def priority_class(priority):
    """Map priority to Bootstrap color class."""
    mapping = {
        'critical': 'danger',
        'high': 'warning',
        'medium': 'primary',
        'low': 'secondary',
    }
    return mapping.get(priority, 'secondary')


@register.filter
def trend_icon(trend):
    """Return Bootstrap icon class for trend direction."""
    mapping = {
        'up': 'bi-arrow-up-short',
        'down': 'bi-arrow-down-short',
        'flat': 'bi-dash',
    }
    return mapping.get(trend, '')


@register.filter
def trend_class(trend):
    """Return CSS class for trend direction."""
    mapping = {
        'up': 'text-success',
        'down': 'text-danger',
        'flat': 'text-muted',
    }
    return mapping.get(trend, 'text-muted')


@register.filter
def action_type_icon(action_type):
    """Return icon for action item type."""
    mapping = {
        'approval': 'bi-check-circle',
        'review': 'bi-eye',
        'signature': 'bi-pen',
        'submission': 'bi-send',
        'response': 'bi-reply',
    }
    return mapping.get(action_type, 'bi-circle')


@register.filter
def metric_value(metric):
    """Format a metric value based on its unit."""
    value = metric.get('value', 0)
    unit = metric.get('unit')
    try:
        val = float(value)
        if unit == 'USD':
            if val >= 1_000_000:
                return f'${val / 1_000_000:,.1f}M'
            if val >= 1_000:
                return f'${val / 1_000:,.0f}K'
            return f'${val:,.0f}'
        if unit == 'days':
            return f'{val:.0f}d'
        if unit == 'percent':
            return f'{val:.0f}%'
        if val == int(val):
            return f'{int(val):,}'
        return f'{val:,.1f}'
    except (ValueError, TypeError):
        return str(value)


@register.simple_tag
def sparkline_data(sparklines, key):
    """Extract sparkline values as a JSON-safe string for Chart.js."""
    import json
    data = sparklines.get(key, {})
    values = data.get('values', [])
    labels = data.get('labels', [])
    return mark_safe(json.dumps({'values': values, 'labels': labels}))
