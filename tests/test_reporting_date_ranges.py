"""
Date-range filtering in the reports.

The rule under test: a report asked for a range ending *today* must include
what happened today. `sale_date` is a DateTimeField, so comparing it against a
bare `date` upper bound means midnight — which silently drops every sale made
during the final day of the range. That failure is invisible: the report
renders happily with no rows, and reads as "no trade" rather than "wrong
query".
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.reporting.services import sales_report, stock_movement_report
from apps.sales.services import confirm_sale, create_sale

pytestmark = pytest.mark.django_db


@pytest.fixture
def sale_today(product, warehouse, cash_customer, batch, pharmacist):
    """A confirmed cash sale timestamped now, i.e. partway through today."""
    sale = create_sale(
        customer=cash_customer,
        warehouse=warehouse,
        lines=[{"product": product, "quantity": Decimal("5")}],
        actor=pharmacist,
    )
    confirm_sale(sale, actor=pharmacist, amount_tendered=Decimal("100000"))
    sale.refresh_from_db()
    return sale


def test_sales_report_includes_todays_sales(sale_today):
    """
    A range whose upper bound is today must contain a sale made today.

    This is the regression: with `sale_date__lte=<date>` the sale at 09:06
    falls after the implied 00:00 cut-off and vanishes from the report.
    """
    today = timezone.localdate()
    report = sales_report(date_from=today - timezone.timedelta(days=7), date_to=today)

    assert report["lines"], "a sale made today was excluded from the report"
    assert report["totals"]["revenue"] == sale_today.total_amount


def test_sales_report_excludes_sales_after_the_range(sale_today):
    """The widened bound must not spill into the following day."""
    yesterday = timezone.localdate() - timezone.timedelta(days=1)
    report = sales_report(
        date_from=yesterday - timezone.timedelta(days=7), date_to=yesterday
    )

    assert not report["lines"], "a sale made today leaked into a range ending yesterday"


def test_stock_movement_report_includes_todays_movements(sale_today):
    """The same bare-date bound applies to the movement ledger."""
    today = timezone.localdate()
    report = stock_movement_report(
        date_from=today - timezone.timedelta(days=7), date_to=today
    )

    assert report["lines"], "movements posted today were excluded from the report"
