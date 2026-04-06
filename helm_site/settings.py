"""
Helm — Executive Dashboard
Django settings
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() in ('true', '1', 'yes')

import secrets as _secrets

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = _secrets.token_hex(25)
    else:
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured('DJANGO_SECRET_KEY must be set in production')

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
if DEBUG:
    ALLOWED_HOSTS.append('testserver')

# Railway provides RAILWAY_PUBLIC_DOMAIN automatically
RAILWAY_DOMAIN = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
if RAILWAY_DOMAIN:
    ALLOWED_HOSTS.append(RAILWAY_DOMAIN)
    ALLOWED_HOSTS.append('.railway.app')

CSRF_TRUSTED_ORIGINS = os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
if RAILWAY_DOMAIN:
    CSRF_TRUSTED_ORIGINS.append(f'https://{RAILWAY_DOMAIN}')
CSRF_TRUSTED_ORIGINS = [o for o in CSRF_TRUSTED_ORIGINS if o]

# ---------------------------------------------------------------------------
# Demo Mode
# ---------------------------------------------------------------------------
DEMO_MODE = os.environ.get('DEMO_MODE', 'False').lower() in ('true', '1', 'yes')
DEMO_ROLES = ['helm_admin', 'helm_director', 'helm_viewer']

# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'django.contrib.sites',
    # Keel (DockLabs shared platform)
    'keel.accounts',
    'keel.core',
    'keel.security',
    'keel.notifications',
    'keel.periods',
    'keel.compliance',
    'keel.reporting',
    'keel.requests',
    # Third party
    'rest_framework',
    'crispy_forms',
    'crispy_bootstrap5',
    'django_filters',
    # Allauth (SSO / MFA)
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.microsoft',
    'allauth.mfa',
    # Project apps
    'core.apps.CoreConfig',
    'dashboard.apps.DashboardConfig',
    'api.apps.ApiConfig',
]

SITE_ID = 1

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'keel.security.middleware.SecurityHeadersMiddleware',
    'keel.security.middleware.FailedLoginMonitor',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'keel.accounts.middleware.ProductAccessMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'keel.core.middleware.AuditMiddleware',
]

ROOT_URLCONF = 'helm_site.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'keel.core.context_processors.site_context',
                'keel.core.context_processors.fleet_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'helm_site.wsgi.application'

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
import dj_database_url

DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
    )
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = 'keel_accounts.KeelUser'
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/helm/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# ---------------------------------------------------------------------------
# Allauth (SSO)
# ---------------------------------------------------------------------------
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'username*', 'password1*', 'password2*']
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = 'optional'
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_ADAPTER = 'keel.core.sso.KeelAccountAdapter'

SOCIALACCOUNT_PROVIDERS = {
    'microsoft': {
        'APP': {
            'client_id': os.environ.get('MICROSOFT_CLIENT_ID', ''),
            'secret': os.environ.get('MICROSOFT_CLIENT_SECRET', ''),
        },
        'TENANT': os.environ.get('MICROSOFT_TENANT_ID', 'common'),
        'SCOPE': ['openid', 'email', 'profile', 'User.Read'],
    },
}

# MFA
MFA_ADAPTER = 'allauth.mfa.adapter.DefaultMFAAdapter'
MFA_SUPPORTED_TYPES = ['totp', 'webauthn', 'recovery_codes']
MFA_TOTP_ISSUER = 'Helm'
MFA_PASSKEY_LOGIN_ENABLED = True

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'en'
TIME_ZONE = 'America/New_York'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ---------------------------------------------------------------------------
# Crispy forms
# ---------------------------------------------------------------------------
CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK = 'bootstrap5'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
if DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
else:
    EMAIL_BACKEND = 'keel.notifications.backends.resend_backend.ResendEmailBackend'

DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'DockLabs <info@docklabs.ai>')
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')

# ---------------------------------------------------------------------------
# Keel
# ---------------------------------------------------------------------------
KEEL_PRODUCT_CODE = 'helm'
KEEL_FLEET_PRODUCTS = [
    {'name': 'Helm', 'label': 'Helm', 'code': 'helm', 'url': '/'},
    {'name': 'Beacon', 'label': 'Beacon', 'code': 'beacon', 'url': '/'},
    {'name': 'Harbor', 'label': 'Harbor', 'code': 'harbor', 'url': '/'},
    {'name': 'Bounty', 'label': 'Bounty', 'code': 'bounty', 'url': '/'},
    {'name': 'Lookout', 'label': 'Lookout', 'code': 'lookout', 'url': '/'},
]
KEEL_PRODUCT_NAME = 'Helm'
KEEL_PRODUCT_ICON = 'bi-compass'
KEEL_PRODUCT_SUBTITLE = 'Executive Dashboard'
KEEL_GATE_ACCESS = True
KEEL_AUDIT_LOG_MODEL = 'core.AuditLog'
KEEL_NOTIFICATION_MODEL = 'core.Notification'
KEEL_NOTIFICATION_PREFERENCE_MODEL = 'core.NotificationPreference'
KEEL_NOTIFICATION_LOG_MODEL = 'core.NotificationLog'
KEEL_API_KEY = os.environ.get('KEEL_API_KEY', '')
KEEL_API_URL = os.environ.get('KEEL_API_URL', 'https://keel.docklabs.ai')

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
}

# ---------------------------------------------------------------------------
# Fleet Product Registry (for dashboard navigation)
# ---------------------------------------------------------------------------
FLEET_PRODUCTS = [
    {'key': 'harbor', 'label': 'Harbor', 'icon': 'bi-bank2', 'url': 'https://harbor.docklabs.ai/dashboard/', 'tagline': 'State Grants'},
    {'key': 'bounty', 'label': 'Bounty', 'icon': 'bi-globe', 'url': 'https://bounty.docklabs.ai/dashboard/', 'tagline': 'Federal Funds'},
    {'key': 'beacon', 'label': 'Beacon', 'icon': 'bi-broadcast', 'url': 'https://beacon.docklabs.ai/dashboard/', 'tagline': 'CRM'},
    {'key': 'admiralty', 'label': 'Admiralty', 'icon': 'bi-shield-check', 'url': 'https://admiralty.docklabs.ai', 'tagline': 'FOIA'},
    {'key': 'manifest', 'label': 'Manifest', 'icon': 'bi-pen', 'url': 'https://manifest.docklabs.ai', 'tagline': 'Signing'},
    {'key': 'lookout', 'label': 'Lookout', 'icon': 'bi-binoculars', 'url': 'https://lookout.docklabs.ai/dashboard/', 'tagline': 'Legislative'},
    {'key': 'purser', 'label': 'Purser', 'icon': 'bi-safe2', 'url': 'https://purser.docklabs.ai', 'tagline': 'Finance'},
    {'key': 'yeoman', 'label': 'Yeoman', 'icon': 'bi-calendar-event', 'url': 'https://yeoman.docklabs.ai', 'tagline': 'Scheduling'},
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
        'dashboard': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SESSION_COOKIE_AGE = 3600
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

if not DEBUG:
    SECURE_SSL_REDIRECT = False
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_REFERRER_POLICY = 'same-origin'
    X_FRAME_OPTIONS = 'DENY'

AUTH_PASSWORD_VALIDATORS[1]['OPTIONS'] = {'min_length': 10}
