"""Dashboard URL configuration."""
from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    # Main views
    path('', views.DashboardView.as_view(), name='index'),
    path('notifications/', views.NotificationInboxView.as_view(), name='notifications'),
    path('period/<uuid:period_id>/', views.PeriodDetailView.as_view(), name='period'),
    path('programs/', views.MyProgramsView.as_view(), name='programs'),

    # htmx partials
    path('partials/action-queue/', views.ActionQueuePartialView.as_view(), name='partial-action-queue'),
    path('partials/alerts/', views.AlertPanelPartialView.as_view(), name='partial-alerts'),
    path('partials/metrics/', views.MetricsGridPartialView.as_view(), name='partial-metrics'),
    path('partials/card/<str:product>/', views.ProductCardPartialView.as_view(), name='partial-card'),
]
