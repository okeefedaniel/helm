"""API URL configuration."""
from django.urls import path
from . import metrics, views

app_name = 'api'

urlpatterns = [
    path('v1/briefing/', views.BriefingAPIView.as_view(), name='briefing'),
    path('v1/dashboard/', views.DashboardDataAPIView.as_view(), name='dashboard-data'),
    path('v1/metrics/', metrics.metrics, name='metrics'),
]
