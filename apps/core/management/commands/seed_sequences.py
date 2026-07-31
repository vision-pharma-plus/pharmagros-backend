"""Create the document numbering series. Idempotent."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.numbering import DEFAULT_SEQUENCES, DocumentSequence, SequenceScope


class Command(BaseCommand):
    help = "Seed document numbering sequences."

    @transaction.atomic
    def handle(self, *args, **options):
        created = existing = 0

        for spec in DEFAULT_SEQUENCES:
            defaults = {
                "label": spec.get("label", ""),
                "prefix": spec.get("prefix", ""),
                "padding": spec.get("padding", 6),
                "scope": spec.get("scope", SequenceScope.YEARLY),
            }
            # get_or_create, never update_or_create: overwriting an existing
            # row would reset current_value and produce duplicate invoice
            # numbers on the next allocation.
            _obj, was_created = DocumentSequence.objects.get_or_create(
                key=spec["key"], defaults=defaults
            )
            if was_created:
                created += 1
            else:
                existing += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Sequences: {created} created, {existing} already present "
                f"({DocumentSequence.objects.count()} total)."
            )
        )
