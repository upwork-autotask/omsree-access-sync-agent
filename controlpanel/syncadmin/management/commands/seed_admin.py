"""Create (or update) the admin login from env or args.

    python manage.py seed_admin --username admin --password secret --email a@b.com
Falls back to env ADMIN_USERNAME / ADMIN_PASSWORD / ADMIN_EMAIL.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or reset the admin (superuser) login."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=os.environ.get("ADMIN_USERNAME", "admin"))
        parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
        parser.add_argument("--email", default=os.environ.get("ADMIN_EMAIL", ""))

    def handle(self, *args, **opts):
        User = get_user_model()
        username = opts["username"]
        password = opts["password"]
        if not password:
            self.stderr.write("No password given (--password or ADMIN_PASSWORD).")
            return
        user, created = User.objects.get_or_create(username=username, defaults={"email": opts["email"]})
        user.is_staff = True
        user.is_superuser = True
        user.email = opts["email"] or user.email
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} admin '{username}'."))
