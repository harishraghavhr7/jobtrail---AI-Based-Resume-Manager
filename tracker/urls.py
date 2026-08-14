from django.urls import path
from . import views

urlpatterns = [
    # Auth urls
    path('', views.login_view, name='root'), # Redirect or login by default
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
    path('logout/', views.logout_view, name='logout'),
    
    # Dashboard url
    path('dashboard/', views.dashboard_view, name='dashboard'),
    
    # Applications CRUD & drag-drop
    path('applications/', views.applications_list_view, name='applications_list'),
    path('applications/new/', views.application_create_view, name='application_create'),
    path('applications/<int:pk>/', views.application_detail_view, name='application_detail'),
    path('applications/<int:pk>/edit/', views.application_edit_view, name='application_edit'),
    path('applications/<int:pk>/delete/', views.application_delete_view, name='application_delete'),
    
    # Pipeline progression
    path('applications/<int:pk>/advance/', views.advance_stage_view, name='advance_stage'),
    path('applications/<int:pk>/status/', views.update_application_status_api, name='update_application_status_api'),
    path('applications/bulk/', views.bulk_action_view, name='bulk_action'),
    
    # AI Match Analysis Polling
    path('applications/<int:pk>/analyze/', views.trigger_analysis_view, name='trigger_analysis'),
    path('applications/<int:pk>/analyze/status/', views.check_analysis_status_view, name='check_analysis_status'),
    path('applications/analyze/preview/', views.preview_match_unsaved_view, name='preview_match_unsaved'),
    
    # Resume Manager
    path('resumes/', views.resume_manager_view, name='resume_manager'),
    path('resumes/<int:pk>/status/', views.check_resume_status_view, name='check_resume_status'),
    path('resumes/<int:pk>/active/', views.set_active_resume_view, name='set_active_resume'),
    path('resumes/<int:pk>/delete-warning/', views.delete_resume_warning_view, name='delete_resume_warning'),
    path('resumes/<int:pk>/delete-confirm/', views.delete_resume_confirm_view, name='delete_resume_confirm'),
    path('resumes/<int:pk>/reparse/', views.reparse_resume_skills_view, name='reparse_resume_skills'),
    
    # Settings & profile
    path('profile/', views.profile_view, name='profile'),
    path('profile/export/', views.export_data_view, name='export_data'),
    path('profile/delete/', views.delete_account_view, name='delete_account'),
]
