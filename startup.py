#!/usr/bin/env python
"""
Startup script for Railway deployment.
Runs collectstatic, migrations, seed (if empty), then gunicorn.
"""
import os
import sys
import subprocess
import time

os.environ['PYTHONUNBUFFERED'] = '1'


def log(msg):
    print(f"[startup] {msg}", flush=True)


def run(cmd, fatal=False):
    log(f"Running: {cmd}")
    try:
        result = subprocess.run(
            cmd, shell=True,
            stdout=sys.stdout, stderr=sys.stderr,
        )
        if result.returncode != 0:
            log(f"Command exited with code {result.returncode}: {cmd}")
            if fatal:
                sys.exit(result.returncode)
            return False
        return True
    except Exception as e:
        log(f"Command failed with exception: {e}")
        if fatal:
            sys.exit(1)
        return False


def main():
    log("=" * 50)
    log("Helm — Executive Dashboard")
    log("Container starting")
    log("=" * 50)

    port = os.environ.get('PORT', '8080')
    manage = f"{sys.executable} manage.py"

    # Diagnostics
    raw_db = os.environ.get('DATABASE_URL', '')
    db_display = f"SET ({raw_db.split('://')[0]}://******)" if '://' in raw_db else 'NOT SET'
    log(f"PORT = {port}")
    log(f"DATABASE_URL = {db_display}")
    log(f"Secret key: {'SET' if os.environ.get('DJANGO_SECRET_KEY') else 'NOT SET'}")
    log(f"Python: {sys.version}")

    # Import Django
    log("Loading Django settings...")
    try:
        import django
        django.setup()
        log("Django loaded successfully")
    except Exception as e:
        log(f"ERROR loading Django: {e}")
        import traceback
        traceback.print_exc(file=sys.stdout)

    # Collect static files
    log("=== Collecting static files ===")
    run(f"{manage} collectstatic --noinput")

    # Start gunicorn early so healthcheck passes
    gunicorn_cmd = (
        f"gunicorn helm_site.wsgi "
        f"--bind 0.0.0.0:{port} "
        f"--workers 2 "
        f"--access-logfile - "
        f"--error-logfile - "
        f"--timeout 120"
    )
    log(f"=== Starting gunicorn on port {port} ===")
    gunicorn_proc = subprocess.Popen(
        gunicorn_cmd, shell=True,
        stdout=sys.stdout, stderr=sys.stderr,
    )
    log(f"Gunicorn started (PID {gunicorn_proc.pid})")
    time.sleep(3)

    if gunicorn_proc.poll() is not None:
        log(f"ERROR: Gunicorn exited with code {gunicorn_proc.returncode}")
        sys.exit(1)

    # Run migrations
    log("=== Running migrations ===")
    run(f"{manage} migrate --noinput")

    # Ensure django.contrib.sites has the correct Site record (required by allauth)
    log("=== Configuring Site object ===")
    try:
        from django.contrib.sites.models import Site
        domain = os.environ.get('SITE_DOMAIN', 'helm.docklabs.ai')
        site, created = Site.objects.update_or_create(
            id=1, defaults={'domain': domain, 'name': 'Helm'},
        )
        log(f"  Site {'created' if created else 'updated'}: {site.domain}")
    except Exception as e:
        log(f"  WARNING: Could not configure Site: {e}")

    # Bootstrap superuser from env vars (idempotent, always resets password)
    if os.environ.get('CREATE_SUPERUSER', '').lower() in ('true', '1', 'yes'):
        run(f"{manage} ensure_superuser")

    # Fetch live feeds from products (or fall back to seed data)
    log("=== Populating feed data ===")
    try:
        from django.conf import settings as _settings
        helm_feed_key = getattr(_settings, 'HELM_FEED_API_KEY', '')

        if helm_feed_key:
            # Always fetch when key is present — works for both prod and demo.
            # Products bypass auth in DEMO_MODE, so the same key works everywhere.
            log("HELM_FEED_API_KEY set — fetching live feeds from products...")
            ok = run(f"{manage} fetch_feeds --parallel")
            if not ok:
                log("Live fetch had errors — falling back to seed data for unfetched products")
                run(f"{manage} seed_helm")
        else:
            from dashboard.models import CachedFeedSnapshot
            if CachedFeedSnapshot.objects.count() == 0:
                log("No API key and no feed data — seeding demo data...")
                run(f"{manage} seed_helm")
            else:
                log(f"Feed data exists ({CachedFeedSnapshot.objects.count()} products)")
    except Exception as e:
        log(f"Feed population failed: {e}")

    log("=== Startup complete, waiting for gunicorn ===")
    gunicorn_proc.wait()
    log(f"Gunicorn exited with code {gunicorn_proc.returncode}")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stdout)
        time.sleep(30)
        sys.exit(1)
