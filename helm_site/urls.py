"""Helm URL Configuration."""
from django.contrib import admin
from django.contrib.auth.views import LoginView
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView, TemplateView

from dashboard.views import DashboardView

from keel.core.views import health_check, robots_txt, LandingView, SuiteLogoutView
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
    path('', LandingView.as_view(
        template_name='landing.html',
        authenticated_redirect='dashboard:index',
        stats=[
            {'value': '9', 'label': 'Products'},
            {'value': '1', 'label': 'Identity'},
            {'value': 'Real-time', 'label': 'Aggregation'},
            {'value': 'Role-based', 'label': 'Access'},
        ],
        features=[
            {'icon': 'bi-bar-chart-line', 'title': 'Cross-Fleet Dashboards',
             'description': 'See real-time metrics from every DockLabs product in a single executive view.',
             'color': 'blue'},
            {'icon': 'bi-check2-square', 'title': 'Action Items',
             'description': 'Pending approvals, overdue tasks, and items requiring attention — across all products.',
             'color': 'teal'},
            {'icon': 'bi-shield-check', 'title': 'Compliance & Alerts',
             'description': 'Monitor compliance posture, security alerts, and operational health from one place.',
             'color': 'yellow'},
        ],
        steps=[
            {'title': 'Sign In Once', 'description': 'One DockLabs identity unlocks every product in the fleet.'},
            {'title': 'Pick a Product', 'description': 'Jump to any product from the fleet switcher — no re-login.'},
            {'title': 'See Aggregated Metrics', 'description': 'Helm rolls up KPIs from every product into your executive dashboard.'},
            {'title': 'Drill Into Details', 'description': 'Click any metric to jump straight into the underlying product.'},
        ],
    ), name='landing'),
    # Custom login/logout views using our styled templates (before allauth)
    path('accounts/login/', LoginView.as_view(
        template_name='account/login.html',
        authentication_form=LoginForm,
    ), name='account_login'),
    path('accounts/logout/', SuiteLogoutView.as_view(), name='account_logout'),
    # Allauth handles everything else (signup, SSO, MFA, password reset)
    path('accounts/', include('allauth.urls')),
    # Keel shared
    path('notifications/', include('keel.notifications.urls')),
    path('keel/requests/', include('keel.requests.urls')),
    # Helm apps
    path('helm/', include('dashboard.urls')),
    # Canonical suite-wide post-login URL. Mounts the real DashboardView
    # directly so the URL bar stays at /dashboard/. The legacy /helm/
    # URL still works for direct navigation.
    path('dashboard/', DashboardView.as_view(), name='dashboard_alias'),
    path('api/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
