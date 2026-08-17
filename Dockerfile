# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

# Use BuildKit cache mount — pip packages are cached across builds
# Even if requirements.txt changes, already-downloaded packages are reused
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=300 -r requirements.txt

COPY . .

EXPOSE 8000

# Run migrations, collect static files, then start gunicorn (production server)
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn jobtrail.wsgi:application --bind 0.0.0.0:8000 --workers 3"]