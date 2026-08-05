"""Create the default expense categories. Idempotent."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounting.services import seed_default_categories


class Command(BaseCommand):
    help = "Seed the default expense categories."

    @transaction.atomic
    def handle(self, *args, **options):
        created, existing = seed_default_categories()
        self.stdout.write(
            self.style.SUCCESS(
                f"Expense categories: {created} created, {existing} already present."
            )
        )
