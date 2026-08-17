import csv
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib import messages
from django.http import HttpResponse, JsonResponse, Http404
from django.db import transaction
from django.db.models import Q, Count
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Profile, Resume, Application, MatchAnalysis, StatusHistory
from .tasks import extract_resume_skills_task, analyze_match_task
from .services import analyze_match, extract_text_from_file, extract_resume_skills

# ----------------- AUTHENTICATION VIEWS -----------------

def signup_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            # Create default profile
            Profile.objects.get_or_create(user=user)
            messages.success(request, "Registration successful. Welcome to JobTrail!")
            return redirect('dashboard')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.capitalize()}: {error}")
    else:
        form = UserCreationForm()
        
    return render(request, 'tracker/login_signup.html', {
        'form': form,
        'is_signup': True
    })

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f"Welcome back, {username}!")
                return redirect('dashboard')
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
        
    return render(request, 'tracker/login_signup.html', {
        'form': form,
        'is_signup': False
    })

@login_required
def logout_view(request):
    logout(request)
    messages.info(request, "Logged out successfully.")
    return redirect('login')


# ----------------- DASHBOARD VIEW -----------------

@login_required
def dashboard_view(request):
    user = request.user
    applications = Application.objects.filter(user=user)
    total_apps = applications.count()
    
    # Interviews are applications that have ever been in interview stages
    # (interview, technical, hr) or currently are.
    interview_stages = ['interview', 'technical', 'hr']
    total_interviews = Application.objects.filter(
        user=user
    ).filter(
        Q(status__in=interview_stages) | Q(history__status__in=interview_stages)
    ).distinct().count()
    
    # Offers are applications that have ever been in offer stage or currently are.
    total_offers = Application.objects.filter(
        user=user
    ).filter(
        Q(status='offer') | Q(history__status='offer')
    ).distinct().count()
    
    # Response Rate = (Interviews + Offers) / Total Applications * 100
    response_rate = 0
    if total_apps > 0:
        response_rate = int(((total_interviews + total_offers) / total_apps) * 100)
        
    # Get last 5 applications for the recent table
    recent_applications = applications.order_by('-created_at')[:5]
    
    # Data for Funnel Chart: Count distinct applications that ever reached each stage
    stages = ['applied', 'oa', 'interview', 'technical', 'hr', 'offer']
    funnel_counts = []
    for stage in stages:
        count = Application.objects.filter(
            user=user
        ).filter(
            Q(status=stage) | Q(history__status=stage)
        ).distinct().count()
        funnel_counts.append(count)
        
    # Data for Applications over time (grouped by week in the last 8 weeks)
    # For simplicity, we can group by creation date for the past 6 months
    # We will compute the counts per month for the last 6 months
    timeline_labels = []
    timeline_counts = []
    now = timezone.now()
    for i in range(5, -1, -1):
        month_date = now - timezone.timedelta(days=i*30)
        month_name = month_date.strftime('%B')
        count = applications.filter(
            created_at__year=month_date.year,
            created_at__month=month_date.month
        ).count()
        timeline_labels.append(month_name)
        timeline_counts.append(count)

    context = {
        'total_apps': total_apps,
        'total_interviews': total_interviews,
        'total_offers': total_offers,
        'response_rate': response_rate,
        'recent_applications': recent_applications,
        'funnel_labels': [s.upper() for s in stages],
        'funnel_data': funnel_counts,
        'timeline_labels': timeline_labels,
        'timeline_data': timeline_counts,
    }
    
    return render(request, 'tracker/dashboard.html', context)


# ----------------- APPLICATIONS LIST & KANBAN -----------------

@login_required
def applications_list_view(request):
    user = request.user
    
    # Handle search, status filter, company filter, date range filter
    search_query = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    company_filter = request.GET.get('company', '')
    date_filter = request.GET.get('date_range', '')
    sort_by = request.GET.get('sort_by', '-created_at')
    view_mode = request.GET.get('view', 'table') # 'table' or 'kanban'
    
    # Base Queryset
    queryset = Application.objects.filter(user=user).select_related('resume', 'match')
    
    # Apply Filters
    if search_query:
        queryset = queryset.filter(
            Q(company__icontains=search_query) | Q(job_role__icontains=search_query)
        )
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if company_filter:
        queryset = queryset.filter(company=company_filter)
        
    if date_filter == '7days':
        queryset = queryset.filter(application_date__gte=timezone.now().date() - timezone.timedelta(days=7))
    elif date_filter == '30days':
        queryset = queryset.filter(application_date__gte=timezone.now().date() - timezone.timedelta(days=30))
        
    # Get distinct list of companies for the filter dropdown
    companies_list = Application.objects.filter(user=user).values_list('company', flat=True).distinct()
    
    # Apply Sorting
    valid_sorts = ['company', '-company', 'job_role', '-job_role', 'application_date', '-application_date', 'status', '-status', 'match__match_percentage', '-match__match_percentage']
    if sort_by in valid_sorts:
        queryset = queryset.order_by(sort_by)
    else:
        queryset = queryset.order_by('-created_at')
        
    # Kanban mode needs applications grouped by status
    kanban_columns = []
    if view_mode == 'kanban':
        stages_def = [
            ('applied', 'Applied', 'bg-blue-500'),
            ('oa', 'Online Assessment', 'bg-purple-500'),
            ('interview', 'Interview', 'bg-amber-500'),
            ('technical', 'Technical Interview', 'bg-orange-500'),
            ('hr', 'HR Round', 'bg-yellow-500'),
            ('offer', 'Offer', 'bg-emerald-500'),
            ('rejected', 'Rejected', 'bg-rose-500'),
        ]
        for col_status, col_name, col_color in stages_def:
            col_cards = [app for app in queryset if app.status == col_status]
            kanban_columns.append({
                'status': col_status,
                'name': col_name,
                'color': col_color,
                'cards': col_cards,
                'count': len(col_cards)
            })
            
    # HTMX partial renders
    if request.headers.get('HX-Request'):
        if view_mode == 'kanban':
            return render(request, 'tracker/partials/kanban_board.html', {
                'kanban_columns': kanban_columns
            })
        else:
            return render(request, 'tracker/partials/applications_table.html', {
                'applications': queryset,
                'sort_by': sort_by
            })
            
    context = {
        'applications': queryset,
        'companies_list': companies_list,
        'kanban_columns': kanban_columns,
        'view_mode': view_mode,
        'search_query': search_query,
        'status_filter': status_filter,
        'company_filter': company_filter,
        'date_filter': date_filter,
        'sort_by': sort_by,
        'status_choices': Application.STATUS_CHOICES
    }
    
    return render(request, 'tracker/applications_list.html', context)


# ----------------- KANBAN DRAG & DROP UPDATE -----------------

@login_required
@require_POST
def update_application_status_api(request, pk):
    """
    API endpoint triggered by SortableJS drag-and-drop.
    Updates the application status and returns success.
    """
    application = get_object_or_404(Application, pk=pk, user=request.user)
    new_status = request.POST.get('status')
    
    valid_statuses = [choice[0] for choice in Application.STATUS_CHOICES]
    if new_status in valid_statuses:
        application._history_note = f"Status updated via Kanban drag-and-drop"
        application.status = new_status
        application.save()
        return HttpResponse(status=204) # No Content, HTMX handles success smoothly
    return HttpResponse("Invalid status", status=400)


# ----------------- BULK ACTIONS -----------------

@login_required
@require_POST
def bulk_action_view(request):
    action = request.POST.get('action')
    selected_ids = request.POST.getlist('selected_ids')
    
    if not selected_ids:
        messages.warning(request, "No applications selected.")
        return redirect('applications_list')
        
    queryset = Application.objects.filter(user=request.user, id__in=selected_ids)
    
    if action == 'delete':
        count = queryset.count()
        queryset.delete()
        messages.success(request, f"Successfully deleted {count} applications.")
        
    elif action.startswith('status_'):
        new_status = action.replace('status_', '')
        valid_statuses = [choice[0] for choice in Application.STATUS_CHOICES]
        if new_status in valid_statuses:
            updated_count = 0
            with transaction.atomic():
                for app in queryset:
                    app._history_note = "Status updated via bulk action"
                    app.status = new_status
                    app.save()
                    updated_count += 1
            messages.success(request, f"Successfully updated status for {updated_count} applications.")
        else:
            messages.error(request, "Invalid status choice.")
            
    return redirect('applications_list')


# ----------------- CREATE & EDIT APPLICATION -----------------

@login_required
def application_create_view(request):
    user = request.user
    resumes = Resume.objects.filter(user=user, is_active=True)
    
    if request.method == 'POST':
        company = request.POST.get('company')
        job_role = request.POST.get('job_role')
        job_url = request.POST.get('job_url')
        application_date = request.POST.get('application_date')
        status = request.POST.get('status')
        resume_id = request.POST.get('resume')
        notes = request.POST.get('notes', '')
        jd_text = request.POST.get('jd_text', '')
        
        # Validation
        if not company or not job_role or not application_date or not status:
            messages.error(request, "Please fill out all required fields.")
            return redirect('application_create')
            
        resume = None
        if resume_id:
            resume = get_object_or_404(Resume, id=resume_id, user=user)
            
        application = Application.objects.create(
            user=user,
            company=company,
            job_role=job_role,
            job_url=job_url,
            application_date=application_date,
            status=status,
            resume=resume,
            notes=notes,
            jd_text=jd_text
        )
        
        # Trigger async Groq match analysis if resume and JD are provided
        if resume and jd_text.strip():
            analyze_match_task.delay(application.id)
            messages.success(request, "Application saved. Match analysis is running in the background.")
        else:
            messages.success(request, "Application saved successfully.")
            
        return redirect('application_detail', pk=application.id)
        
    context = {
        'resumes': resumes,
        'status_choices': Application.STATUS_CHOICES,
        'today': timezone.now().date().strftime('%Y-%m-%d')
    }
    return render(request, 'tracker/application_form.html', context)


@login_required
def application_edit_view(request, pk):
    user = request.user
    application = get_object_or_404(Application, pk=pk, user=user)
    resumes = Resume.objects.filter(user=user, is_active=True)
    
    if request.method == 'POST':
        application.company = request.POST.get('company')
        application.job_role = request.POST.get('job_role')
        application.job_url = request.POST.get('job_url')
        application.application_date = request.POST.get('application_date')
        application.status = request.POST.get('status')
        application.notes = request.POST.get('notes', '')
        
        old_resume = application.resume
        old_jd = application.jd_text
        
        resume_id = request.POST.get('resume')
        if resume_id:
            application.resume = get_object_or_404(Resume, id=resume_id, user=user)
        else:
            application.resume = None
            
        application.jd_text = request.POST.get('jd_text', '')
        
        application._history_note = "Application details edited"
        application.save()
        
        # Re-trigger analysis if resume or JD changed
        if (application.resume != old_resume or application.jd_text != old_jd) and application.resume and application.jd_text.strip():
            # Delete old match analysis
            MatchAnalysis.objects.filter(application=application).delete()
            analyze_match_task.delay(application.id)
            messages.success(request, "Application updated. Re-running match analysis in the background.")
        else:
            messages.success(request, "Application updated successfully.")
            
        return redirect('application_detail', pk=application.id)
        
    context = {
        'application': application,
        'resumes': resumes,
        'status_choices': Application.STATUS_CHOICES,
        'application_date_formatted': application.application_date.strftime('%Y-%m-%d')
    }
    return render(request, 'tracker/application_form.html', context)


@login_required
@require_POST
def application_delete_view(request, pk):
    application = get_object_or_404(Application, pk=pk, user=request.user)
    application.delete()
    messages.success(request, "Application deleted successfully.")
    return redirect('applications_list')


# ----------------- APPLICATION DETAIL -----------------

@login_required
def application_detail_view(request, pk):
    application = get_object_or_404(Application, pk=pk, user=request.user)
    history = application.history.all()
    
    # We will build pipeline list
    pipeline_stages = ['applied', 'oa', 'interview', 'technical', 'hr', 'offer']
    
    # Find current stage index
    current_index = -1
    if application.status in pipeline_stages:
        current_index = pipeline_stages.index(application.status)
    elif application.status in ['rejected', 'withdrawn']:
        # If rejected/withdrawn, we find the last historical stage before rejection
        last_stages = history.exclude(status__in=['rejected', 'withdrawn']).values_list('status', flat=True)
        if last_stages:
            last_status = last_stages[0]
            if last_status in pipeline_stages:
                current_index = pipeline_stages.index(last_status)
                
    context = {
        'application': application,
        'history': history,
        'pipeline_stages': pipeline_stages,
        'current_index': current_index,
        'is_inactive_status': application.status in ['rejected', 'withdrawn']
    }
    return render(request, 'tracker/application_detail.html', context)


# ----------------- ADVANCE PIPELINE STAGE API -----------------

@login_required
@require_POST
def advance_stage_view(request, pk):
    """
    Endpoint triggered by clicking a step in the pipeline stepper widget.
    Advances/updates the application status.
    """
    application = get_object_or_404(Application, pk=pk, user=request.user)
    new_stage = request.POST.get('stage')
    
    pipeline_stages = ['applied', 'oa', 'interview', 'technical', 'hr', 'offer', 'rejected', 'withdrawn']
    if new_stage in pipeline_stages:
        application._history_note = f"Stage transitioned to {new_stage.upper()} via pipeline widget"
        application.status = new_stage
        application.save()
        
        # For HTMX response, re-render the detail page pipeline and history partials or redirect
        if request.headers.get('HX-Request'):
            # Just do a full page refresh trigger or redirect to the detail page to update history
            response = HttpResponse()
            response['HX-Redirect'] = redirect('application_detail', pk=application.id).url
            return response
            
        return redirect('application_detail', pk=application.id)
    return HttpResponse("Invalid stage", status=400)


# ----------------- ASYNC AI MATCH ANALYSIS API -----------------

@login_required
@require_POST
def trigger_analysis_view(request, pk):
    """
    Manually triggers match analysis for an application.
    Used for the "Re-analyze Match" button or "Analyze Match" initial click.
    """
    application = get_object_or_404(Application, pk=pk, user=request.user)
    if not application.resume:
        return HttpResponse("Please link a resume first", status=400)
        
    # Delete old MatchAnalysis if it exists
    MatchAnalysis.objects.filter(application=application).delete()
    
    # Trigger background Celery task
    analyze_match_task.delay(application.id)
    
    # Return loading status HTML
    return render(request, 'tracker/partials/match_analysis_loading.html', {
        'application': application
    })


@login_required
def check_analysis_status_view(request, pk):
    """
    Polled by HTMX every 2s to check if MatchAnalysis is ready.
    """
    application = get_object_or_404(Application, pk=pk, user=request.user)
    
    try:
        match_analysis = application.match
        # If match is ready and loaded (not error state)
        if match_analysis.match_percentage > 0 or match_analysis.ai_summary:
            return render(request, 'tracker/partials/match_analysis_card.html', {
                'application': application,
                'match': match_analysis
            })
    except MatchAnalysis.DoesNotExist:
        pass
        
    # If not ready, continue showing the loader with HTMX polling
    return render(request, 'tracker/partials/match_analysis_loading.html', {
        'application': application
    })


@login_required
@require_POST
def preview_match_unsaved_view(request):
    """
    Endpoint for running match analysis on unsaved applications.
    Accepts resume_id and jd_text, runs analysis synchronously since it is fast.
    """
    resume_id = request.POST.get('resume')
    jd_text = request.POST.get('jd_text', '')
    
    if not resume_id or not jd_text.strip():
        return HttpResponse("<div class='text-rose-400 text-sm'>Please select a resume and fill the Job Description.</div>")
        
    resume = get_object_or_404(Resume, id=resume_id, user=request.user)
    
    # Trigger text extraction and parsing synchronously if not parsed yet
    if not resume.parsed_skills:
        try:
            raw_text = extract_text_from_file(resume.file)
            resume.raw_text = raw_text
            resume.parsed_skills = extract_resume_skills_task(resume.id) # or call directly
            resume.save()
        except Exception as e:
            return HttpResponse(f"<div class='text-rose-400 text-sm'>Failed to parse resume skills: {str(e)}</div>")
            
    # Run Match Analysis synchronously
    result = analyze_match(resume.parsed_skills, jd_text)
    
    # Render preview card
    return render(request, 'tracker/partials/match_analysis_card.html', {
        'match': result,
        'preview_mode': True
    })


# ----------------- RESUME MANAGER -----------------

@login_required
def resume_manager_view(request):
    user = request.user
    resumes = Resume.objects.filter(user=user).order_by('-created_at')
    
    if request.method == 'POST':
        version_name = request.POST.get('version_name')
        file = request.FILES.get('file')
        set_active = request.POST.get('set_active') == 'true'
        
        if not version_name or not file:
            messages.error(request, "Please enter a version name and select a resume file.")
            return redirect('resume_manager')
            
        # File validations: Max 5MB, PDF/DOCX only
        file_size = file.size / (1024 * 1024)
        if file_size > 5.0:
            messages.error(request, "File size exceeds 5MB limit.")
            return redirect('resume_manager')
            
        filename = file.name.lower()
        if not filename.endswith('.pdf') and not filename.endswith('.docx') and not filename.endswith('.txt'):
            messages.error(request, "Only PDF, DOCX, or TXT files are allowed.")
            return redirect('resume_manager')
            
        with transaction.atomic():
            # If set_active, make all other user resumes inactive
            if set_active:
                Resume.objects.filter(user=user).update(is_active=False)
                
            resume = Resume.objects.create(
                user=user,
                version_name=version_name,
                file=file,
                is_active=set_active or (not Resume.objects.filter(user=user, is_active=True).exists())
            )
            
        # Trigger background text parsing
        extract_resume_skills_task.delay(resume.id)
        messages.success(request, f"Resume '{version_name}' uploaded. Extracting text in the background...")
        return redirect('resume_manager')
        
    context = {
        'resumes': resumes
    }
    return render(request, 'tracker/resume_manager.html', context)


@login_required
def check_resume_status_view(request, pk):
    """
    Polled by HTMX to check if resume parsing is completed.
    """
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    
    if request.headers.get('HX-Request'):
        # If skills list is populated, it is "Ready", otherwise "Parsing..."
        if resume.parsed_skills:
            return render(request, 'tracker/partials/resume_card.html', {
                'resume': resume
            })
        else:
            return render(request, 'tracker/partials/resume_card_loading.html', {
                'resume': resume
            })
            
    return JsonResponse({
        'status': 'Ready' if resume.parsed_skills else 'Parsing',
        'skills': resume.parsed_skills
    })


@login_required
@require_POST
def set_active_resume_view(request, pk):
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    
    with transaction.atomic():
        # Set all other user resumes to inactive
        Resume.objects.filter(user=request.user).update(is_active=False)
        # Set this one to active
        resume.is_active = True
        resume.save()
        
    messages.success(request, f"Resume '{resume.version_name}' set as active.")
    return redirect('resume_manager')


@login_required
def delete_resume_warning_view(request, pk):
    """
    Checks if a resume is linked to applications.
    If yes, returns HTML prompt to either reassign applications or leave them NULL.
    """
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    linked_apps = resume.applications.all()
    
    if not linked_apps.exists():
        # No links, can delete safely. Standard confirmation modal.
        return render(request, 'tracker/partials/resume_delete_confirm.html', {
            'resume': resume,
            'has_links': False
        })
        
    # Has links, offer options: reassign or set null
    other_resumes = Resume.objects.filter(user=request.user).exclude(pk=pk)
    return render(request, 'tracker/partials/resume_delete_confirm.html', {
        'resume': resume,
        'has_links': True,
        'linked_count': linked_apps.count(),
        'other_resumes': other_resumes
    })


@login_required
@require_POST
def delete_resume_confirm_view(request, pk):
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    action = request.POST.get('action') # 'set_null' or 'reassign'
    reassign_to_id = request.POST.get('reassign_to')
    
    linked_apps = resume.applications.all()
    
    with transaction.atomic():
        if linked_apps.exists() and action == 'reassign' and reassign_to_id:
            reassign_resume = get_object_or_404(Resume, id=reassign_to_id, user=request.user)
            linked_apps.update(resume=reassign_resume)
        elif linked_apps.exists() and action == 'set_null':
            linked_apps.update(resume=None)
            
        resume.delete()
        
    messages.success(request, "Resume deleted successfully.")
    return redirect('resume_manager')


@login_required
@require_POST
def reparse_resume_skills_view(request, pk):
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    # Clear old skills and run background parse again
    resume.parsed_skills = []
    resume.save()
    
    extract_resume_skills_task.delay(resume.id)
    messages.success(request, f"Re-parsing skills for '{resume.version_name}'...")
    return redirect('resume_manager')


# ----------------- PROFILE, SETTINGS & EXPORT -----------------

@login_required
def profile_view(request):
    user = request.user
    profile, created = Profile.objects.get_or_create(user=user)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'update_profile':
            first_name = request.POST.get('first_name', '')
            last_name = request.POST.get('last_name', '')
            email = request.POST.get('email', '')
            target_role = request.POST.get('target_role', '')
            
            user.first_name = first_name
            user.last_name = last_name
            user.email = email
            user.save()
            
            profile.target_role = target_role
            profile.save()
            
            messages.success(request, "Profile updated successfully.")
            return redirect('profile')
            
        elif action == 'change_password':
            current_pass = request.POST.get('current_password')
            new_pass = request.POST.get('new_password')
            confirm_pass = request.POST.get('confirm_password')
            
            if not user.check_password(current_pass):
                messages.error(request, "Incorrect current password.")
            elif new_pass != confirm_pass:
                messages.error(request, "New passwords do not match.")
            elif len(new_pass) < 6:
                messages.error(request, "Password must be at least 6 characters.")
            else:
                user.set_password(new_pass)
                user.save()
                login(request, user) # keep logged in
                messages.success(request, "Password changed successfully.")
            return redirect('profile')
            
    return render(request, 'tracker/profile.html', {
        'profile': profile
    })


@login_required
def export_data_view(request):
    export_format = request.GET.get('format', 'csv')
    applications = Application.objects.filter(user=request.user).select_related('resume', 'match')
    
    if export_format == 'json':
        data = []
        for app in applications:
            app_data = {
                'company': app.company,
                'job_role': app.job_role,
                'job_url': app.job_url,
                'application_date': app.application_date.strftime('%Y-%m-%d'),
                'status': app.get_status_display(),
                'notes': app.notes,
                'jd_text': app.jd_text,
                'resume_version': app.resume.version_name if app.resume else None,
                'match_percentage': app.match.match_percentage if hasattr(app, 'match') else None,
                'strong_matches': app.match.strong_matches if hasattr(app, 'match') else [],
                'missing_skills': app.match.missing_skills if hasattr(app, 'match') else [],
                'ai_summary': app.match.ai_summary if hasattr(app, 'match') else '',
            }
            data.append(app_data)
            
        response = HttpResponse(json.dumps(data, indent=2), content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="jobtrail_export_{request.user.username}.json"'
        return response
        
    else: # CSV format
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="jobtrail_export_{request.user.username}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Company', 'Job Role', 'Job URL', 'Application Date', 'Status', 'Resume Version', 'Match %', 'AI Summary', 'Notes'])
        
        for app in applications:
            match_pct = app.match.match_percentage if hasattr(app, 'match') else 'N/A'
            ai_sum = app.match.ai_summary if hasattr(app, 'match') else 'N/A'
            resume_ver = app.resume.version_name if app.resume else 'N/A'
            
            writer.writerow([
                app.company,
                app.job_role,
                app.job_url or '',
                app.application_date.strftime('%Y-%m-%d'),
                app.get_status_display(),
                resume_ver,
                match_pct,
                ai_sum,
                app.notes
            ])
            
        return response


@login_required
@require_POST
def delete_account_view(request):
    user = request.user
    user.delete()
    messages.success(request, "Your account has been deleted successfully. We are sorry to see you go!")
    return redirect('login')
