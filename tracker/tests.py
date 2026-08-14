import os
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import Profile, Resume, Application, MatchAnalysis, StatusHistory
from .services import clean_and_parse_json, extract_resume_skills, analyze_match

class ModelTests(TestCase):
    
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='password123')
        
    def test_profile_auto_creation(self):
        """Verify profile model is automatically created on user signup."""
        profile = getattr(self.user, 'profile', None)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.user, self.user)
        
    def test_status_history_on_application_create(self):
        """Verify a StatusHistory entry is created when a new application is saved."""
        app = Application.objects.create(
            user=self.user,
            company='Acme Corp',
            job_role='Python Dev',
            jd_text='Required skills: Python, Django.',
            application_date=timezone.now().date(),
            status='applied'
        )
        history = app.history.all()
        self.assertEqual(history.count(), 1)
        self.assertEqual(history[0].status, 'applied')
        self.assertIn('Initial status set to', history[0].note)

    def test_status_history_on_application_status_change(self):
        """Verify StatusHistory log is updated only when status changes."""
        app = Application.objects.create(
            user=self.user,
            company='Acme Corp',
            job_role='Python Dev',
            jd_text='Required skills: Python, Django.',
            application_date=timezone.now().date(),
            status='applied'
        )
        
        # Edit something else - should NOT create another log
        app.notes = "Referred by Alice."
        app.save()
        self.assertEqual(app.history.count(), 1)
        
        # Edit status - SHOULD create another log
        app.status = 'oa'
        app.save()
        self.assertEqual(app.history.count(), 2)
        self.assertEqual(app.history.first().status, 'oa')
        self.assertIn('Status updated to', app.history.first().note)


class ServicesTests(TestCase):
    
    def test_clean_and_parse_json_success(self):
        """Test parsing of wrapped markdown JSON strings."""
        raw_md = '```json\n{"match_percentage": 80, "strong_matches": ["Python"], "missing_skills": [], "ai_summary": "Good."}\n```'
        parsed = clean_and_parse_json(raw_md)
        self.assertEqual(parsed['match_percentage'], 80)
        self.assertEqual(parsed['strong_matches'], ["Python"])
        
    def test_clean_and_parse_json_raw_fallback(self):
        """Test parsing of naked JSON strings."""
        raw_json = '{"match_percentage": 50, "strong_matches": [], "missing_skills": [], "ai_summary": "Ok."}'
        parsed = clean_and_parse_json(raw_json)
        self.assertEqual(parsed['match_percentage'], 50)
        
    def test_mock_services_fallback(self):
        """Verify that when no valid Groq key is configured, fallback values are returned."""
        # This will test our mock logic since the settings GROQ_API_KEY in tests is typically dummy/empty
        skills = extract_resume_skills("This is a resume text for John Doe.")
        self.assertIn("Python", skills)
        self.assertIn("Django", skills)
        
        analysis = analyze_match(skills, "Looking for a Python Developer experienced in Django.")
        self.assertGreaterEqual(analysis['match_percentage'], 70)
        self.assertEqual(analysis['strong_matches'], ["Python", "Django"])


class ViewsTests(TestCase):
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='password123')
        
    def test_dashboard_redirects_unauthenticated(self):
        """Dashboard should redirect to login page for anonymous clients."""
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)
        
    def test_dashboard_accessible_authenticated(self):
        """Dashboard page is successfully loaded when logged in."""
        self.client.login(username='tester', password='password123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tracker/dashboard.html')
        
    def test_resume_manager_requires_login(self):
        """Resume manager page requires authentication."""
        response = self.client.get(reverse('resume_manager'))
        self.assertEqual(response.status_code, 302)
