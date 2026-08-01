"""
Convert catalogue prices from tax-exclusive (HT) to tax-inclusive (TTC).

Staff price products the way the counter and the shelf edge do: the number
entered is what the customer hands over. Before this migration `selling_price`
and `wholesale_price` held the HT base and VAT was added on top at sale time,
which forced whoever maintained the catalogue to do the extraction by hand.

What changes is the MEANING of two existing columns, not their shape — so
there is no schema operation here, only data. `Medicine.price_for()` now
performs the inverse extraction at the single point where a catalogue price
becomes a document line, leaving every downstream computation (compute_line,
the tax summary, the OBR fiscal payload) working on the HT base it requires.

Deliberately NOT touched: PriceHistory. Those rows are immutable by design and
exist so an auditor can reconcile an old invoice against the price in force on
its date. Those old invoices were raised on the HT basis, so rewriting the
history to TTC would falsify exactly the reconciliation it exists to support.
The rows stay HT and the marker row written below records where the basis
changed, so a reader can tell which side of the line any entry falls on.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

HUNDRED = Decimal("100")
INTERNAL_DP = Decimal("0.0001")
# Matches apps.core.money.ROUNDING — OHADA practice rounds half away from zero.
ROUNDING = ROUND_HALF_UP

MARKER_REASON = "VAT basis change: catalogue prices converted HT -> TTC"


def _effective_rate(medicine) -> Decimal:
    """
    Mirror of `Medicine.effective_vat_rate`.

    Model properties are unavailable on the historical models a migration gets,
    so the rule is restated here. Using the raw `vat_rate` instead would inflate
    every exempt product by 18% for tax it has never carried — the single most
    damaging mistake available in this migration.
    """
    if medicine.is_vat_exempt:
        return Decimal("0")
    return Decimal(medicine.vat_rate or 0)


def _to_ttc(price, rate: Decimal) -> Decimal:
    price = Decimal(price or 0)
    if rate == 0 or price == 0:
        return price
    return (price * (Decimal("1") + rate / HUNDRED)).quantize(
        INTERNAL_DP, rounding=ROUNDING
    )


def _to_ht(price, rate: Decimal) -> Decimal:
    price = Decimal(price or 0)
    if rate == 0 or price == 0:
        return price
    return (price / (Decimal("1") + rate / HUNDRED)).quantize(
        INTERNAL_DP, rounding=ROUNDING
    )


def forwards(apps, schema_editor):
    Medicine = apps.get_model("catalog", "Medicine")
    PriceHistory = apps.get_model("catalog", "PriceHistory")

    # Idempotence guard. Applying the conversion twice would compound the VAT
    # (180 -> 212.40 -> 250.63) and silently overcharge every customer, so the
    # marker row is checked before anything is written.
    if PriceHistory.objects.filter(reason=MARKER_REASON).exists():
        return

    converted = []
    for medicine in Medicine.objects.all().iterator():
        rate = _effective_rate(medicine)
        if rate == 0:
            # Exempt and zero-rated products are already TTC == HT.
            continue

        new_selling = _to_ttc(medicine.selling_price, rate)
        new_wholesale = _to_ttc(medicine.wholesale_price, rate)
        if (
            new_selling == medicine.selling_price
            and new_wholesale == medicine.wholesale_price
        ):
            continue

        medicine.selling_price = new_selling
        medicine.wholesale_price = new_wholesale
        converted.append(medicine)

    if converted:
        Medicine.objects.bulk_update(
            converted, ["selling_price", "wholesale_price"], batch_size=500
        )

    # One marker row, not one per product: this records the basis change itself
    # rather than pretending each product was individually repriced. It doubles
    # as the guard above. `medicine` is required, so it is anchored to the first
    # converted product purely to satisfy the FK.
    anchor = converted[0] if converted else Medicine.objects.first()
    if anchor is not None:
        PriceHistory.objects.create(
            medicine=anchor,
            old_unit_cost=Decimal("0"),
            new_unit_cost=Decimal("0"),
            old_selling_price=Decimal("0"),
            new_selling_price=Decimal("0"),
            reason=MARKER_REASON,
        )


def backwards(apps, schema_editor):
    Medicine = apps.get_model("catalog", "Medicine")
    PriceHistory = apps.get_model("catalog", "PriceHistory")

    marker = PriceHistory.objects.filter(reason=MARKER_REASON)
    if not marker.exists():
        return

    reverted = []
    for medicine in Medicine.objects.all().iterator():
        rate = _effective_rate(medicine)
        if rate == 0:
            continue

        medicine.selling_price = _to_ht(medicine.selling_price, rate)
        medicine.wholesale_price = _to_ht(medicine.wholesale_price, rate)
        reverted.append(medicine)

    if reverted:
        Medicine.objects.bulk_update(
            reverted, ["selling_price", "wholesale_price"], batch_size=500
        )

    # PriceHistory.delete() is blocked on the concrete model, but historical
    # models carry no custom methods, so the marker can be cleared here — which
    # is what makes the migration genuinely re-appliable.
    marker.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_bilingual_fields_and_batch_capture"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
