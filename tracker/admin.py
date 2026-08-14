from django.contrib import admin
from .models import Profile, Resume, Application, MatchAnalysis, StatusHistory

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'target_role', 'created_at')
    search_fields = ('user__username', 'target_role')

@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ('user', 'version_name', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__username', 'version_name')

class MatchAnalysisInline(admin.StackedInline):
    model = MatchAnalysis
    extra = 0

class StatusHistoryInline(admin.TabularInline):
    model = StatusHistory
    extra = 0
    readonly_fields = ('changed_at',)

@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ('company', 'job_role', 'user', 'status', 'application_date', 'created_at')
    list_filter = ('status', 'application_date')
    search_fields = ('company', 'job_role', 'user__username')
    inlines = [MatchAnalysisInline, StatusHistoryInline]

@admin.register(MatchAnalysis)
class MatchAnalysisAdmin(admin.ModelAdmin):
    list_display = ('application', 'match_percentage', 'created_at')
    search_fields = ('application__company', 'application__job_role')

@admin.register(StatusHistory)
class StatusHistoryAdmin(admin.ModelAdmin):
    list_display = ('application', 'status', 'changed_at', 'note')
    list_filter = ('status', 'changed_at')
    search_fields = ('application__company', 'application__job_role')
