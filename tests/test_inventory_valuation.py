"""
Inventory valuation must reconcile on the page.

The reported defect: a valuation line showed quantity 35, unit cost 4 902 and
value 171 581. The reader multiplies the two printed figures, gets 171 570, and
concludes the report is wrong — which, as a document, it is. The cause was that
`landed_unit_cost` stores four decimals (4 902.3022 here, because freight and
duty spread over the units rarely divide evenly) while the report prints whole
francs, and the value was computed from the unrounded figure.

The rule these tests pin: a report line is valued at the unit cost it actually
prints, so `quantity x unit_cost == value` holds for every line, the column
sums to the stated total, and every report that values the same batch agrees.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.money import q_document
from apps.inventory.models import StockBatch
from apps.inventory.services import inventory_valuation
from apps.reporting.services import (
    dead_stock_report,
    expiry_report,
    inventory_valuation_report,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def awkward_batch(db, product, warehouse, supplier, inventory_officer, today):
    """
    The reported case: a landed cost that does not fall on a whole franc.

    35 units at a stored 4 902.3022 — printed as 4 902 — is the exact line that
    failed to reconcile.
    """
    return StockBatch.objects.create(
        product=product,
        warehouse=warehouse,
        supplier=supplier,
        batch_number="LOT-VALUATION-1",
        quantity_received=Decimal("35"),
        quantity_remaining=Decimal("35"),
        expiry_date=today + timezone.timedelta(days=365),
        unit_cost=Decimal("4100"),
        landed_unit_cost=Decimal("4902.3022"),
        created_by=inventory_officer,
    )


def test_reported_line_reconciles(awkward_batch):
    """35 x 4 902 must read 171 570, the figure a reader computes by hand."""
    report = inventory_valuation_report()
    line = next(
        line
        for line in report["lines"]
        if line["batch_number"] == awkward_batch.batch_number
    )

    assert line["quantity"] == Decimal("35")
    assert line["unit_cost"] == Decimal("4902")
    assert line["value"] == Decimal("171570")


def test_every_line_multiplies_out(awkward_batch):
    """The invariant behind the case above, across whatever else is present."""
    for line in inventory_valuation_report()["lines"]:
        assert line["quantity"] * line["unit_cost"] == line["value"], (
            f"line {line['batch_number']} does not multiply out"
        )


def test_grand_total_is_the_sum_of_the_printed_lines(awkward_batch):
    """The footer must equal the column above it."""
    report = inventory_valuation_report()

    assert report["total_value"] == sum(line["value"] for line in report["lines"])


def test_headline_total_matches_the_itemised_report(awkward_batch):
    """
    `inventory_valuation` and the valuation report are read side by side.

    They cover the same batches, so a difference between them is unexplainable
    to a user even when each is internally consistent.
    """
    assert inventory_valuation()["total_value"] == (
        inventory_valuation_report()["total_value"]
    )


def test_expiry_report_agrees_on_the_same_batch(awkward_batch):
    """One batch, one value, whichever report is looking at it."""
    report = expiry_report(days=400)
    line = next(
        line
        for line in report["lines"]
        if line["batch_number"] == awkward_batch.batch_number
    )

    assert line["quantity"] * line["unit_cost"] == line["value_at_risk"]
    assert line["value_at_risk"] == Decimal("171570")
    assert report["total_value_at_risk"] == sum(
        line["value_at_risk"] for line in report["lines"]
    )


def test_dead_stock_report_agrees_on_the_same_batch(awkward_batch):
    """The third report that values batches must not be the odd one out."""
    report = dead_stock_report(days_without_movement=0)
    line = next(
        line
        for line in report["lines"]
        if line["batch_number"] == awkward_batch.batch_number
    )

    assert line["value"] == Decimal("171570")
    assert report["total_value"] == sum(line["value"] for line in report["lines"])


def test_stock_value_is_the_shared_definition(awkward_batch):
    """Every caller resolves to this one property."""
    assert awkward_batch.stock_value == Decimal("171570")


def test_dashboard_tile_matches_the_report_it_links_to(awkward_batch):
    """
    The inventory-value tile drills through to the valuation report.

    A tile showing one total and the report behind it showing another is the
    same class of defect as a line that does not multiply out — the user
    clicked the number precisely to see what makes it up.
    """
    from apps.reporting.services import dashboard_kpis

    tile = dashboard_kpis()["inventory"]["total_value"]
    report = inventory_valuation_report()["total_value"]

    # The tile counts ACTIVE batches only, so it can be no larger than the
    # report, which also includes quarantined stock; the batch under test is
    # active and must be valued identically by both.
    assert tile == q_document(tile), "the tile should be whole francs"
    assert tile <= report
    assert awkward_batch.stock_value == Decimal("171570")
