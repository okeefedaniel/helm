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


def run(args, fatal=False):
    pretty = ' '.join(args)
    log(f"Running: {pretty}")
    try:
        result = subprocess.run(
            args, shell=False,
            stdout=sys.stdout, stderr=sys.stderr,
        )
        if result.returncode != 0:
            log(f"Command exited with code {result.returncode}: {pretty}")
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
    manage = [sys.executable, 'manage.py']

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
    run(manage + ['collectstatic', '--noinput'])

    # Start gunicorn early so healthcheck passes.
    # shell=False (direct exec) so SIGTERM from Railway reaches gunicorn
    # without a /bin/sh wrapper swallowing it.
    gunicorn_cmd = [
        'gunicorn', 'helm_site.wsgi',
        '--bind', f'0.0.0.0:{port}',
        '--workers', '2',
        '--access-logfile', '-',
        '--error-logfile', '-',
        '--timeout', '120',
    ]
    log(f"=== Starting gunicorn on port {port} ===")
    gunicorn_proc = subprocess.Popen(
        gunicorn_cmd, shell=False,
        stdout=sys.stdout, stderr=sys.stderr,
    )
    log(f"Gunicorn started (PID {gunicorn_proc.pid})")
    time.sleep(3)

    if gunicorn_proc.poll() is not None:
        log(f"ERROR: Gunicorn exited with code {gunicorn_proc.returncode}")
        sys.exit(1)

    # Pre-migrate audit — show what's about to run, catch obvious migration-state
    # drift (orphan app labels, missing dependencies) before the irreversible step.
    # Added 2026-04-25 after a `core` → `helm_core` rename mismatch took prod down.
    log("=== Pre-migrate audit (showmigrations --plan) ===")
    try:
        res = subprocess.run(
            manage + ['showmigrations', '--plan'],
            shell=False, capture_output=True, text=True,
        )
        combined = (res.stdout or '') + (res.stderr or '')
        for line in combined.splitlines()[-40:]:
            log(line)
    except Exception as e:
        log(f"showmigrations --plan failed: {e}")

    # Run migrations
    # MUST be fatal — see keel/CLAUDE.md "Startup failures MUST be fatal."
    log("=== Running migrations ===")
    run(manage + ['migrate', '--noinput'], fatal=True)

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
        run(manage + ['ensure_superuser'])

    # Fetch live feeds from products (or fall back to seed data)
    log("=== Populating feed data ===")
    try:
        from django.conf import settings as _settings
        helm_feed_key = getattr(_settings, 'HELM_FEED_API_KEY', '')

        if helm_feed_key:
            # Always fetch when key is present — works for both prod and demo.
            # Products bypass auth in DEMO_MODE, so the same key works everywhere.
            log("HELM_FEED_API_KEY set — fetching live feeds from products...")
            # fetch_feeds runs in parallel by default; --sequential disables it.
            # Earlier code passed --parallel which doesn't exist as a flag and
            # caused every boot to fall through to seed_helm (clobbering live
            # data with demo data). Don't reintroduce that flag.
            ok = run(manage + ['fetch_feeds'])
            if not ok:
                demo_mode = getattr(_settings, 'DEMO_MODE', False)
                debug = getattr(_settings, 'DEBUG', False)
                if demo_mode or debug:
                    log("Live fetch had errors — seeding demo data (DEMO_MODE/DEBUG).")
                    run(manage + ['seed_helm'])
                else:
                    # Production: never overwrite live data with seed fixtures.
                    # If a fetch fails here, the existing CachedFeedSnapshot
                    # rows (or empty state) are kept; ops sees the warning and
                    # can re-run fetch_feeds manually.
                    log("Live fetch had errors — refusing to seed in production.")
        else:
            from dashboard.models import CachedFeedSnapshot
            demo_mode = getattr(_settings, 'DEMO_MODE', False)
            debug = getattr(_settings, 'DEBUG', False)
            if not demo_mode and not debug:
                # Production-like env: never silently fall back to seed data.
                # Fail loud so ops notices the missing HELM_FEED_API_KEY.
                log("WARNING: HELM_FEED_API_KEY is not set and DEMO_MODE is off.")
                log("Refusing to auto-seed demo data in production. Set HELM_FEED_API_KEY.")
            elif CachedFeedSnapshot.objects.count() == 0:
                log("No API key and no feed data — seeding demo data (DEMO_MODE/DEBUG)...")
                run(manage + ['seed_helm'])
            else:
                log(f"Feed data exists ({CachedFeedSnapshot.objects.count()} products)")
    except Exception as e:
        log(f"Feed population failed: {e}")

    # Sync scheduled-job declarations into the keel_scheduling DB so the
    # /scheduling/ dashboard stays in step with code. Idempotent — preserves
    # admin-edited fields (enabled, notes) across deploys.
    log("=== Syncing scheduled jobs ===")
    run(manage + ['sync_scheduled_jobs'], fatal=False)

    # Demo PM data — only in DEMO_MODE so prod is never seeded.
    # Idempotent: skips if any of the four demo project slugs already exist.
    if os.environ.get('DEMO_MODE', '').lower() in ('true', '1', 'yes'):
        log("=== DEMO_MODE — seeding demo project lifecycle ===")
        run(manage + ['seed_demo_projects'], fatal=False)

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
