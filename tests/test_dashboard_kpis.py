"""
Dashboard KPI correctness.

Two defects are pinned here.

The first is the date filter: `dashboard_kpis` computed "today" and "this
month" from the clock and accepted no range at all, so the dashboard filter
could not move the figures. A filter that silently returns unfiltered numbers
is worse than no filter — the user believes they are looking at the period
they picked.

The second is margin. VAT is collected on behalf of the state and is not
revenue, so margin is `(total - tax) - cost`. The dashboard used
`total - cost`, overstating margin by the entire VAT amount. `Sale.gross_margin`,
`sales_report` and `profit_and_loss` all already agreed on the correct form;
only the dashboard disagreed, which is the worst place for it to happen because
it is the number management reads first.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.reporting.services import dashboard_kpis, revenue_trend
from apps.sales.services import confirm_sale, create_sale

pytestmark = pytest.mark.django_db


@pytest.fixture
def sale_today(product, warehouse, cash_customer, batch, pharmacist):
    """A confirmed cash sale timestamped now."""
    sale = create_sale(
        customer=cash_customer,
        warehouse=warehouse,
        lines=[{"product": product, "quantity": Decimal("5")}],
        actor=pharmacist,
    )
    confirm_sale(sale, actor=pharmacist, amount_tendered=Decimal("100000"))
    sale.refresh_from_db()
    return sale


def test_margin_excludes_vat(sale_today):
    """
    Dashboard margin must match the sale's own definition.

    The product carries VAT, so `total - cost` and `(total - tax) - cost`
    differ; asserting against `Sale.gross_margin` ties the dashboard to the one
    definition the rest of the system uses rather than restating the arithmetic.
    """
    kpis = dashboard_kpis()

    assert sale_today.tax_amount > 0, "fixture must carry VAT for this to be meaningful"
    assert kpis["sales"]["daily_margin"] == sale_today.gross_margin
    assert kpis["sales"]["monthly_margin"] == sale_today.gross_margin


def test_trend_margin_excludes_vat(sale_today):
    """The chart under the tiles must not disagree with them."""
    points = revenue_trend(days=7)

    assert points, "a sale made today should appear in the trend"
    assert sum(p["margin"] for p in points) == sale_today.gross_margin


def test_range_bounds_the_sales_figures(sale_today):
    """An explicit range must actually filter — the reported bug."""
    today = timezone.localdate()

    included = dashboard_kpis(date_from=today, date_to=today)
    assert included["sales"]["monthly_revenue"] == sale_today.total_amount
    assert included["sales"]["monthly_transactions"] == 1

    # A window that closed before the sale happened must report nothing.
    old = today - timezone.timedelta(days=10)
    excluded = dashboard_kpis(date_from=old, date_to=old + timezone.timedelta(days=1))
    assert excluded["sales"]["monthly_revenue"] == Decimal("0")
    assert excluded["sales"]["monthly_transactions"] == 0
    assert excluded["sales"]["daily_revenue"] == Decimal("0")


def test_range_ending_today_includes_today(sale_today):
    """
    The end bound is inclusive of its whole day.

    `sale_date` is a DateTimeField; a bare date bound means midnight and would
    drop every sale made during the final day — the same defect already fixed
    in the other reports.
    """
    today = timezone.localdate()
    kpis = dashboard_kpis(date_from=today - timezone.timedelta(days=7), date_to=today)

    assert kpis["sales"]["monthly_revenue"] == sale_today.total_amount


def test_unfiltered_dashboard_keeps_its_original_meaning(sale_today):
    """With no range the tiles still mean today and the month to date."""
    kpis = dashboard_kpis()
    today = timezone.localdate()

    assert kpis["sales"]["is_filtered"] is False
    assert kpis["sales"]["period_start"] == today.replace(day=1)
    assert kpis["sales"]["period_end"] == today
    assert kpis["sales"]["daily_revenue"] == sale_today.total_amount


def test_filtered_flag_and_period_are_reported(sale_today):
    """The response states the window it covers, so the UI can label it."""
    start = timezone.localdate() - timezone.timedelta(days=3)
    end = timezone.localdate()
    kpis = dashboard_kpis(date_from=start, date_to=end)

    assert kpis["sales"]["is_filtered"] is True
    assert kpis["sales"]["period_start"] == start
    assert kpis["sales"]["period_end"] == end


def test_positions_ignore_the_range(sale_today):
    """
    Stock and receivables are balances as at now, not flows.

    Filtering them to a past window would report a stock level the warehouse
    does not hold, so they must stay identical across ranges.
    """
    old = timezone.localdate() - timezone.timedelta(days=30)
    unfiltered = dashboard_kpis()
    filtered = dashboard_kpis(date_from=old, date_to=old)

    assert filtered["inventory"] == unfiltered["inventory"]
    assert filtered["receivables"] == unfiltered["receivables"]
