web: python manage.py collectstatic --noinput && python manage.py migrate --noinput && gunicorn helm_site.wsgi --bind 0.0.0.0:$PORT --workers 2 --access-logfile - --error-logfile - --timeout 120
