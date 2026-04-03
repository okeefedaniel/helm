"""Helm URL Configuration."""
from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView, TemplateView

from keel.core.views import health_check, robots_txt
from keel.core.demo import demo_login_view
from core.forms import LoginForm

from django.utils.translation import gettext_lazy as _

admin.site.site_header = _('Helm Administration')
admin.site.site_title = _('Helm Admin')
admin.site.index_title = _('Executive Dashboard')

urlpatterns = [
    path('robots.txt', robots_txt, name='robots_txt'),
    path('health/', health_check, name='health_check'),
    path('demo-login/', demo_login_view, name='demo_login'),
    path('admin/', admin.site.urls),
    # Root — landing page for visitors, redirect to dashboard for logged-in users
    path('', TemplateView.as_view(template_name='landing.html'), name='landing'),
    # Custom login/logout views using our styled templates (before allauth)
    path('accounts/login/', LoginView.as_view(
        template_name='account/login.html',
        authentication_form=LoginForm,
    ), name='account_login'),
    path('accounts/logout/', LogoutView.as_view(), name='account_logout'),
    # Allauth handles everything else (signup, SSO, MFA, password reset)
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
