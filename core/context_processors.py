"""Context processors for Helm."""
from django.conf import settings
from keel.core.context_processors import site_context  # noqa: F401


def helm_feature_flags(request):
    """Expose optional-feature flags to every template."""
    return {
        'helm_tasks_enabled': getattr(settings, 'HELM_TASKS_ENABLED', False),
    }


def fleet_context(request):
    """Inject fleet product registry and user access into every template context."""
    all_products = getattr(settings, 'KEEL_FLEET_PRODUCTS', [])
    user = getattr(request, 'user', None)

    if user and user.is_authenticated:
        if user.is_superuser:
            user_products = all_products
        else:
            accessible = set(user.get_products())
            user_products = [p for p in all_products if p['code'] in accessible]
    else:
        user_products = []

    # Fleet health for the navbar badge
    fleet_health = 'gray'
    fleet_health_class = 'secondary'
    if user and user.is_authenticated:
        from dashboard.models import CachedFeedSnapshot
        total = CachedFeedSnapshot.objects.count()
        stale = CachedFeedSnapshot.objects.filter(is_stale=True).count()
        if total > 0:
            if stale == 0:
                fleet_health = 'green'
                fleet_health_class = 'success'
            elif stale <= 2:
                fleet_health = 'yellow'
                fleet_health_class = 'warning'
            else:
                fleet_health = 'red'
                fleet_health_class = 'danger'

    return {
        'fleet_products': all_products,
        'user_products': user_products,
        'fleet_health': fleet_health,
        'fleet_health_class': fleet_health_class,
    }
