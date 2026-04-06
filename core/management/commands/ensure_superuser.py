"""Bootstrap a superuser from environment variables.

Idempotent: creates the user if missing, resets password if it already exists.
Also ensures a ProductAccess record exists for the helm product.

Usage:
    SUPERUSER_USERNAME=dokadmin SUPERUSER_EMAIL=dok@example.com SUPERUSER_PASSWORD=secret \
    python manage.py ensure_superuser
"""
import os

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = 'Create or update a superuser from env vars.'

    def handle(self, *args, **options):
        username = os.environ.get('SUPERUSER_USERNAME', '').strip()
        email = os.environ.get('SUPERUSER_EMAIL', '').strip()
        password = os.environ.get('SUPERUSER_PASSWORD', '').strip()

        if not all([username, email, password]):
            self.stdout.write(self.style.WARNING(
                'Skipping: SUPERUSER_USERNAME, SUPERUSER_EMAIL, and '
                'SUPERUSER_PASSWORD must all be set.'
            ))
            return

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email': email,
                'is_staff': True,
                'is_superuser': True,
            },
        )

        # Always reset password (handles both new and existing users)
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.save()

        # Ensure ProductAccess exists for helm
        try:
            from keel.accounts.models import ProductAccess
            ProductAccess.objects.get_or_create(
                user=user,
                product='helm',
                defaults={'role': 'admin', 'is_active': True},
            )
        except Exception:
            pass  # ProductAccess table may not exist yet

        if created:
            self.stdout.write(self.style.SUCCESS(f'Created superuser "{username}"'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Updated superuser "{username}" password'))
