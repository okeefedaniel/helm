from django.urls import path

from . import calendar_views, public_views, views

app_name = 'tasks'

urlpatterns = [
    path('', views.my_tasks, name='my_tasks'),
    path('inbox/', views.inbox, name='inbox'),
    path('inbox/<int:pk>/claim/', views.inbox_claim, name='inbox_claim'),
    path('promote/', views.promote, name='promote'),

    # Calendar — must come before any <slug:slug>/ route that could swallow it.
    path('calendar/', calendar_views.calendar_index, name='calendar_index'),
    path('calendar/events.json', calendar_views.calendar_events_json, name='calendar_events_json'),
    path('calendar.ics', calendar_views.calendar_ical, name='calendar_ical'),

    # Project-level surface (must come before <slug:slug>/ to avoid swallowing).
    path('projects/', views.project_list, name='project_list'),
    path('projects/new/', views.project_create, name='project_create'),
    path('projects/archived/', views.archived_projects, name='archived_projects'),

    # ADD-5 — Project Online (PWA) import wizard. Staff-only.
    path(
        'import/project-online/',
        views.import_project_online_view,
        name='import_project_online',
    ),

    # Project detail + lifecycle endpoints.
    path('projects/<slug:slug>/', views.project_detail, name='project_detail'),
    path('projects/<slug:slug>/tasks/new/', views.task_create, name='task_create'),
    path('projects/<slug:slug>/claim/', views.claim_project_view, name='claim_project'),
    path('projects/<slug:slug>/release/', views.release_project_view, name='release_project'),
    path('projects/<slug:slug>/transition/', views.project_transition_view, name='project_transition'),
    path('projects/<slug:slug>/archive/', views.archive_project_view, name='archive_project'),
    path('projects/<slug:slug>/unarchive/', views.unarchive_project_view, name='unarchive_project'),
    path('projects/<slug:slug>/collaborators/', views.project_collaborators_view, name='project_collaborators'),
    path(
        'projects/<slug:slug>/collaborators/<uuid:collab_id>/remove/',
        views.project_collaborator_remove_view,
        name='project_collaborator_remove',
    ),
    path('projects/<slug:slug>/notes/', views.project_notes_view, name='project_notes'),
    path('projects/<slug:slug>/attachments/', views.project_attachments_view, name='project_attachments'),

    # Phase 7 export endpoints — CSV (task list) + PDF (status report).
    path('projects/<slug:slug>/export.csv', views.export_project_csv, name='export_project_csv'),
    path('projects/<slug:slug>/export.pdf', views.export_project_pdf, name='export_project_pdf'),

    # ADD-2 — FOIA tolling controls (only meaningful for kind=FOIA projects).
    path('projects/<slug:slug>/foia/toll/', views.foia_toll_view, name='foia_toll'),
    path('projects/<slug:slug>/foia/untoll/', views.foia_untoll_view, name='foia_untoll'),

    # ADD-4 — AI project summary. GET = cached, POST = force refresh.
    path('projects/<slug:slug>/summarize/', views.summarize_project_view, name='summarize_project'),

    # ADD-3 — public transparency toggle. LEAD-only.
    path(
        'projects/<slug:slug>/visibility/',
        public_views.toggle_public_visibility,
        name='toggle_public_visibility',
    ),

    # ADD-7 — Granicus GovQA push hook (FOIA projects). LEAD-only.
    # Gated by GRANICUS_GOVQA_URL + GRANICUS_GOVQA_API_KEY settings.
    path(
        'projects/<slug:slug>/govqa-push/',
        views.push_to_govqa_view,
        name='push_to_govqa',
    ),

    # Task-level surface.
    path('t/<int:pk>/', views.task_detail, name='task_detail'),
    path('t/<int:pk>/edit/', views.task_edit, name='task_edit'),
    path('t/<int:pk>/delete/', views.task_delete, name='task_delete'),
    path('t/<int:pk>/status/', views.task_status, name='task_status'),  # legacy alias
    path('t/<int:pk>/transition/', views.task_transition_view, name='task_transition'),
    path('t/<int:pk>/reorder/', views.task_reorder, name='task_reorder'),
    path('t/<int:pk>/collaborators/add/', views.collaborator_add, name='collaborator_add'),
    path('t/<int:pk>/collaborators/<uuid:collab_id>/remove/', views.collaborator_remove, name='collaborator_remove'),

    # Dashboard widget HTMX endpoint
    path('partials/my-tasks/', views.my_tasks_widget, name='partial_my_tasks'),
]
