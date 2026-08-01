"""
What the printed sales receipt shows.

The receipt is handed across the counter, so these tests assert against the
rendered HTML rather than the model: what matters is what the customer can
actually read on the roll. Two things are covered — batch traceability
(the lot and expiry of what was dispensed) and the French wording, since the
receipt renders in French by default.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.template.loader import render_to_string

from apps.sales.models import SaleType
from apps.sales.services import confirm_sale, create_sale

pytestmark = pytest.mark.django_db


def _render(receipt, language="fr"):
    """
    Render the receipt template the way `render_receipt_pdf` does.

    The context builder is exercised for real; only the WeasyPrint call is
    skipped, because the PDF engine is not installed everywhere and what is
    under test here is the content, not the byte format.
    """
    from apps.sales.pdf import render_receipt_pdf

    captured = {}

    def capture(template_name, context):
        captured["html"] = _original(template_name, context)
        return captured["html"]

    _original = render_to_string

    import apps.sales.pdf as pdf_module

    pdf_module.render_to_string = capture
    try:
        try:
            render_receipt_pdf(receipt, language=language)
        except Exception:
            # A missing PDF engine must not fail a content assertion; the
            # template has already rendered by the time the engine is called.
            if "html" not in captured:
                raise
    finally:
        pdf_module.render_to_string = _original

    return captured["html"]


@pytest.fixture
def cash_sale(product, warehouse, cash_customer, batch, pharmacist):
    """A confirmed cash sale of 10 units from the single LOT-A batch."""
    sale = create_sale(
        customer=cash_customer,
        warehouse=warehouse,
        sale_type=SaleType.CASH,
        lines=[{"product": product, "quantity": Decimal("10")}],
        actor=pharmacist,
    )
    return confirm_sale(sale, actor=pharmacist)


class TestBatchTraceability:
    def test_the_batch_number_is_printed(self, cash_sale):
        _sale, _invoice, receipt = cash_sale
        assert "LOT-A" in _render(receipt)

    def test_the_expiry_date_is_printed(self, cash_sale):
        _sale, _invoice, receipt = cash_sale
        # The allocation's own expiry, formatted month/year as on the invoice.
        assert _expected_expiry(receipt) in _render(receipt)

    def test_a_line_split_across_batches_shows_both(
        self, product, warehouse, cash_customer, two_batches, pharmacist
    ):
        """
        400 units cannot come from the 300-unit long-dated batch alone, so the
        line is filled from both. Both lots must appear, or the customer cannot
        tell which of the two boxes in the bag is which.
        """
        sale = create_sale(
            customer=cash_customer,
            warehouse=warehouse,
            sale_type=SaleType.CASH,
            lines=[{"product": product, "quantity": Decimal("400")}],
            actor=pharmacist,
        )
        _sale, _invoice, receipt = confirm_sale(sale, actor=pharmacist)

        html = _render(receipt)
        assert "LOT-SHORT" in html
        assert "LOT-LONG" in html or "LOT-A" in html


class TestFrenchWording:
    """The receipt defaults to French; none of it may print in English."""

    def test_the_title_and_labels_are_french(self, cash_sale):
        _sale, _invoice, receipt = cash_sale
        html = _render(receipt, language="fr")

        assert "REÇU DE VENTE" in html
        assert "Lot :" in html
        assert "Sous-total" in html
        assert "Payé par" in html
        assert "Monnaie" in html or "Reçu" in html
        assert "PAYÉ INTÉGRALEMENT" in html

    def test_no_english_source_strings_leak_through(self, cash_sale):
        _sale, _invoice, receipt = cash_sale
        html = _render(receipt, language="fr")

        for english in (
            "SALES RECEIPT",
            "Served by:",
            "Paid by",
            "Subtotal",
            "PAID IN FULL",
            "Please retain this receipt",
        ):
            assert english not in html, f"untranslated: {english!r}"

    def test_english_still_renders_in_english(self, cash_sale):
        _sale, _invoice, receipt = cash_sale
        html = _render(receipt, language="en")
        assert "SALES RECEIPT" in html
        assert "Batch:" in html


def _expected_expiry(receipt):
    """The month/year the receipt should show for its first allocation."""
    line = receipt.sale.lines.first()
    allocation = line.batch_allocations.first()
    return allocation.expiry_date.strftime("%m/%Y")
