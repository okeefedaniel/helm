"""WSGI config for Helm."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'helm_site.settings')
application = get_wsgi_application()
