"""Context processors for Helm."""
from django.conf import settings
from keel.core.context_processors import site_context  # noqa: F401


def fleet_context(request):
    """Inject fleet product registry into every template context."""
    return {
        'fleet_products': getattr(settings, 'FLEET_PRODUCTS', []),
    }
