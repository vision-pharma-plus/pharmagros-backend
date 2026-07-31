"""
Repair accounts whose password column holds an unhashed value.

Users created through the admin API before the UserCreateSerializer.create()
fix had their raw password written straight into the password column. Those
accounts cannot log in — check_password() reads the column as a hash — and the
stored value is a plaintext credential sitting in the database.

This command re-hashes the value in place, so the password the administrator
originally set continues to work, and flags the account so the holder must
change it at next sign-in (the plaintext may have reached backups or logs).

Idempotent: rows already holding a valid hash are skipped, so it is safe to
re-run on every deploy.
"""

from django.contrib.auth.hashers import identify_hasher
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User


def is_hashed(value: str) -> bool:
    """True when the stored value is a recognised password hash."""
    if not value:
        return False
    try:
        identify_hasher(value)
    except ValueError:
        return False
    return True


class Command(BaseCommand):
    help = "Re-hash any user passwords that were stored as plaintext."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report affected accounts without modifying them.",
        )
        parser.add_argument(
            "--no-force-change",
            action="store_true",
            help=(
                "Do not set must_change_password. Only appropriate if you are "
                "certain the plaintext value was never exposed."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force_change = not options["no_force_change"]

        # Unusable passwords ("!...") are a legitimate state, not corruption.
        affected = [
            user
            for user in User.objects.all()
            if user.has_usable_password() and not is_hashed(user.password)
        ]

        if not affected:
            self.stdout.write(self.style.SUCCESS("No plaintext passwords found."))
            return

        for user in affected:
            if dry_run:
                # The plaintext itself is never printed: this output is likely
                # to end up in deploy logs.
                self.stdout.write(f"  would re-hash: {user.email}")
                continue

            user.set_password(user.password)  # the column holds the raw value
            user.password_changed_at = timezone.now()
            fields = ["password", "password_changed_at", "updated_at"]

            if force_change:
                user.must_change_password = True
                fields.append("must_change_password")

            user.save(update_fields=fields)
            self.stdout.write(f"  re-hashed: {user.email}")

        verb = "would be repaired" if dry_run else "repaired"
        self.stdout.write(
            self.style.SUCCESS(f"{len(affected)} account(s) {verb}.")
        )
        if dry_run:
            self.stdout.write("Dry run — no changes were written.")
