from django.urls import path

from . import views

app_name = 'tasks'

urlpatterns = [
    path('', views.my_tasks, name='my_tasks'),
    path('inbox/', views.inbox, name='inbox'),
    path('promote/', views.promote, name='promote'),
    path('projects/', views.project_list, name='project_list'),
    path('projects/new/', views.project_create, name='project_create'),
    path('projects/<slug:slug>/', views.project_detail, name='project_detail'),
    path('projects/<slug:slug>/tasks/new/', views.task_create, name='task_create'),
    path('t/<int:pk>/', views.task_detail, name='task_detail'),
    path('t/<int:pk>/edit/', views.task_edit, name='task_edit'),
    path('t/<int:pk>/delete/', views.task_delete, name='task_delete'),
    path('t/<int:pk>/status/', views.task_status, name='task_status'),
    path('t/<int:pk>/reorder/', views.task_reorder, name='task_reorder'),
    path('t/<int:pk>/collaborators/add/', views.collaborator_add, name='collaborator_add'),
    path('t/<int:pk>/collaborators/<uuid:collab_id>/remove/', views.collaborator_remove, name='collaborator_remove'),
    # Dashboard widget HTMX endpoint
    path('partials/my-tasks/', views.my_tasks_widget, name='partial_my_tasks'),
]
