"""
Cash sale receipt rendering.

A receipt is not a small invoice. It is handed over at the counter while the
customer waits, so it is rendered at 80 mm — the width of the roll in every
counter printer — rather than on A4. The page is continuous: its height grows
with the number of lines instead of paginating, because a receipt that spills
onto a second sheet is a receipt half of which gets thrown away.

The fiscal document for a sale, where one is required, is the invoice. This
prints the commercial proof of payment, and says so on its face.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation

from apps.core.money import format_bif, q_document

# The PDF engine and the amount-in-words renderer are shared with invoicing
# rather than reimplemented: a receipt and the invoice that may later be raised
# for the same sale must spell the same total the same way.
from apps.invoicing.pdf import (  # noqa: F401  (PDFEngineUnavailable re-exported)
    PDFEngineUnavailable,
    _html_to_pdf,
    amount_in_words,
)


def render_receipt_pdf(receipt, *, language: str | None = None) -> bytes:
    """
    Render a sales receipt to PDF bytes.

    Language defaults to French, matching the invoice path — a receipt handed
    over the counter should not change language with whoever is on shift.

    Every copy renders identically. Reprints are counted and audited (see
    `services.register_receipt_print`) but not marked on the page: the customer
    holding a reprint is holding the same document, not a lesser one.
    """
    language = language or "fr"
    sale = receipt.sale

    with translation.override(language):
        lines = []
        sale_lines = sale.lines.select_related("product").prefetch_related(
            "batch_allocations"
        )
        for line in sale_lines:
            # Shown VAT-inclusive: it is the price on the shelf edge and the
            # one the customer expects to recognise. The tax total is broken
            # out in the summary below rather than per line, which is what
            # fits in 80 mm and what a counter customer actually reads.
            unit_price = Decimal(line.unit_price or 0)
            tax_rate = Decimal(line.tax_rate or 0)
            unit_price_ttc = unit_price * (1 + tax_rate / 100)

            # Batch and expiry are traceability: the customer holding the
            # receipt must be able to match what is in the bag to what was
            # dispensed, and a pharmacist handling a recall or a return works
            # from these two fields. Formatted as one "LOT (MM/YYYY)" entry per
            # allocation so a line filled from two batches stays unambiguous —
            # the invoice prints the same batches, in the same order.
            batches = [
                {
                    "batch_number": allocation.batch_number,
                    "expiry": allocation.expiry_date.strftime("%m/%Y"),
                }
                for allocation in line.batch_allocations.all()
            ]

            lines.append(
                {
                    "description": line.product.display_name,
                    "product_code": line.product.product_code,
                    "quantity": line.quantity,
                    "unit_of_measure": line.product.unit_of_measure.code,
                    "discount_percent": line.discount_percent,
                    "unit_price_display": format_bif(unit_price_ttc),
                    "line_total_display": format_bif(line.line_total),
                    "batches": batches,
                }
            )

        cashier = receipt.issued_by
        cashier_name = (
            (cashier.get_full_name() or cashier.email) if cashier else ""
        )

        # An invoice raised against this receipt is printed on it, so the two
        # documents can be tied together by hand as well as in the system.
        invoice = receipt.invoice

        context = {
            "receipt": receipt,
            "sale": sale,
            "lines": lines,
            "company": settings.COMPANY,
            "cashier": cashier_name,
            "invoice": invoice,
            "LANGUAGE_CODE": language,
            "totals": {
                "subtotal": format_bif(sale.subtotal),
                "discount": format_bif(sale.discount_amount),
                "tax": format_bif(sale.tax_amount),
                "total": format_bif(sale.total_amount),
                "tendered": format_bif(receipt.amount_tendered),
                "change": format_bif(receipt.change_given),
            },
            # Rounded to the franc for the words line, matching the printed
            # total rather than the internal 4 dp figure behind it.
            "amount_in_words": amount_in_words(
                q_document(sale.total_amount), language
            ),
        }

        html = render_to_string("pdf/receipt.html", context)

    return _html_to_pdf(html)
