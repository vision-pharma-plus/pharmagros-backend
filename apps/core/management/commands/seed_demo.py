"""
Create the administrator account.

Seeds nothing else — no catalogue, partners, stock or transactions. Idempotent:
re-running leaves an existing account untouched unless --reset-passwords is
passed.

    python manage.py seed_demo
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

ADMIN_PASSWORD = "Demo!2026#Pharma"

# (email, first name, last name, role code)
ADMIN_USER = ("visionpharmaplus@gmail.com", "Aline", "Ndayishimiye", "system-administrator")


class Command(BaseCommand):
    help = "Seed the administrator account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Reset the admin password even if the account already exists.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.accounts.models import Role, User

        email, first, last, role_code = ADMIN_USER

        admin = User.objects.filter(email=email).first()
        if admin is None:
            admin = User.objects.create_user(
                email=email, password=ADMIN_PASSWORD,
                first_name=first, last_name=last,
            )
            admin.roles.add(Role.objects.get(code=role_code))
            created = True
        else:
            created = False
            if options["reset_passwords"]:
                admin.set_password(ADMIN_PASSWORD)
                admin.save(update_fields=["password"])

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Admin account {'created' if created else 'already present'}.")
        )
        self.stdout.write("")
        self.stdout.write("Sign in with:")
        self.stdout.write(f"  {email:34} {role_code}")
        self.stdout.write("")
        self.stdout.write(f"  Password: {ADMIN_PASSWORD}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Demo credential — change this password before any real deployment."
            )
        )
