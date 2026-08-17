FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir --default-timeout=300 -r requirements.txt

COPY . .

EXPOSE 8000

# Run migrations, collect static files, then start gunicorn (production server)
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn jobtrail.wsgi:application --bind 0.0.0.0:8000 --workers 3"]