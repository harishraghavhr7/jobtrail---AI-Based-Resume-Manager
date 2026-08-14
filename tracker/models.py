from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

# Profile model extending django's built-in User
class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    target_role = models.CharField(max_length=200, blank=True, null=True, help_text="e.g. Backend Engineer")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"

# Automatic Profile creation hook
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    # Ensure profile exists before saving
    if not hasattr(instance, 'profile'):
        Profile.objects.create(user=instance)
    instance.profile.save()

# Resume model storing files and skill extractions
class Resume(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='resumes')
    version_name = models.CharField(max_length=100, help_text="e.g. ML-focused v2")
    file = models.FileField(upload_to='resumes/')
    raw_text = models.TextField(blank=True, default='', help_text="Extracted text from resume")
    parsed_skills = models.JSONField(default=list, blank=True, help_text="AI-extracted skills list")
    is_active = models.BooleanField(default=True, help_text="Soft-delete or current active flag")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.version_name}"

# Application model tracking job applications
class Application(models.Model):
    STATUS_CHOICES = [
        ('applied', 'Applied'),
        ('oa', 'Online Assessment'),
        ('interview', 'Interview'),
        ('technical', 'Technical Interview'),
        ('hr', 'HR Round'),
        ('offer', 'Offer'),
        ('rejected', 'Rejected'),
        ('withdrawn', 'Withdrawn'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='applications')
    company = models.CharField(max_length=200)
    job_role = models.CharField(max_length=200)
    job_url = models.URLField(blank=True, null=True)
    jd_text = models.TextField(help_text="Full Job Description text")
    application_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='applied')
    resume = models.ForeignKey(Resume, null=True, blank=True, on_delete=models.SET_NULL, related_name='applications')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.company} - {self.job_role} ({self.user.username})"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None
        if not is_new:
            try:
                old_status = Application.objects.get(pk=self.pk).status
            except Application.DoesNotExist:
                pass
        
        super().save(*args, **kwargs)
        
        # Track status history transitions
        if is_new or old_status != self.status:
            # We can pass custom notes in the object to override default
            history_note = getattr(self, '_history_note', '')
            if not history_note:
                history_note = f"Initial status set to {self.get_status_display()}" if is_new else f"Status updated to {self.get_status_display()}"
            
            StatusHistory.objects.create(
                application=self,
                status=self.status,
                note=history_note
            )

# MatchAnalysis model storing the results of AI match analysis
class MatchAnalysis(models.Model):
    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name='match')
    match_percentage = models.IntegerField(default=0, help_text="0-100 match rating")
    strong_matches = models.JSONField(default=list, blank=True, help_text="List of matching skills")
    missing_skills = models.JSONField(default=list, blank=True, help_text="List of missing skills")
    ai_summary = models.TextField(blank=True, default='', help_text="AI summary paragraph")
    raw_response = models.JSONField(null=True, blank=True, help_text="Raw Groq response for debugging")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Match for {self.application.company} - {self.application.job_role} ({self.match_percentage}%)"

# StatusHistory model tracking state timelines
class StatusHistory(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='history')
    status = models.CharField(max_length=50)
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"{self.application.company} - {self.status} at {self.changed_at}"
