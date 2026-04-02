"""Helm URL Configuration."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

from keel.core.views import health_check, robots_txt

from django.utils.translation import gettext_lazy as _

admin.site.site_header = _('Helm Administration')
admin.site.site_title = _('Helm Admin')
admin.site.index_title = _('Executive Dashboard')

urlpatterns = [
    path('robots.txt', robots_txt, name='robots_txt'),
    path('health/', health_check, name='health_check'),
    path('admin/', admin.site.urls),
    # Root redirects to dashboard
    path('', RedirectView.as_view(url='/helm/', permanent=False)),
    # Allauth
    path('accounts/', include('allauth.urls')),
    # Keel shared
    path('notifications/', include('keel.notifications.urls')),
    path('keel/requests/', include('keel.requests.urls')),
    # Helm apps
    path('helm/', include('dashboard.urls')),
    path('api/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
