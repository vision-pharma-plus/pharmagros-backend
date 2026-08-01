"""
Create the cash sale receipt numbering series.

Without this row the first cash sale on an existing deployment fails at
`next_number("sales.sales_receipt")`, which raises rather than inventing a
series — a document number is not something to guess at. `seed_sequences`
would also create it, but relying on an operator remembering to re-run a
management command after deploying is how a counter ends up unable to sell.
"""

from django.db import migrations

SEQUENCE_KEY = "sales.sales_receipt"


def create_sequence(apps, schema_editor):
    DocumentSequence = apps.get_model("core", "DocumentSequence")
    # get_or_create, never update_or_create: an installation that already ran
    # `seed_sequences` has this row with a live counter on it, and overwriting
    # it would reset current_value and reissue receipt numbers already given
    # to customers.
    DocumentSequence.objects.get_or_create(
        key=SEQUENCE_KEY,
        defaults={
            "label": "Reçu de vente",
            "prefix": "RV-{yyyy}-",
            "padding": 6,
            "scope": "YEARLY",
        },
    )


def drop_sequence(apps, schema_editor):
    # Only ever removed if it was never used. A series that has issued numbers
    # is part of the audit record, and dropping it would orphan every receipt
    # traced back to it.
    DocumentSequence = apps.get_model("core", "DocumentSequence")
    DocumentSequence.objects.filter(key=SEQUENCE_KEY, current_value=0).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0002_salesreceipt"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_sequence, drop_sequence),
    ]
