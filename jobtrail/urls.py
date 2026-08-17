from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('tracker.urls')),
]

# Serve uploaded media files (resumes) in all environments
# In production, media is stored at MEDIA_ROOT and served directly by Django/gunicorn
# For persistent storage across deploys, mount a Docker volume: -v /home/ubuntu/jobtrail_media:/app/media
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
