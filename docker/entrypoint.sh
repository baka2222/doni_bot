#!/bin/sh
set -e

if [ "$1" = "web" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
  if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py ensure_admin
  fi
  python manage.py seed_trigger_phrases
  exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --access-logfile -
fi

if [ "$1" = "scanner" ]; then
  exec python manage.py run_scanner
fi

exec "$@"
