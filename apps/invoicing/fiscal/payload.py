"""
Mapping an Invoice onto the OBR declaration schema.

This module is the single place where our vocabulary meets the OBR's. Nothing
else in the codebase should know that a credit note is declared as type "FA"
or that cash is payment method 1 — when the schema changes, it changes here.

Amounts are rendered at document precision (whole francs). The declaration
must agree with the paper the customer holds, so it is built from the same
rounded figures the PDF prints rather than from internal 4 dp values.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings

from apps.core.money import q_document

from ..models import InvoiceType, PaymentMethod

# --- OBR document types ----------------------------------------------------
# FN is an ordinary sale, FA a correcting document (credit note), RC a
# refund settled in cash.
DOC_TYPE_NORMAL = "FN"
DOC_TYPE_CORRECTION = "FA"
DOC_TYPE_REFUND = "RC"

DOCUMENT_TYPES = {
    InvoiceType.STANDARD: DOC_TYPE_NORMAL,
    InvoiceType.DEBIT_NOTE: DOC_TYPE_NORMAL,
    InvoiceType.CREDIT_NOTE: DOC_TYPE_CORRECTION,
}

# --- OBR payment methods ---------------------------------------------------
PAYMENT_CODES = {
    PaymentMethod.CASH: "1",
    PaymentMethod.BANK_TRANSFER: "2",
    PaymentMethod.CHEQUE: "2",
    PaymentMethod.CARD: "2",
    PaymentMethod.MOBILE_MONEY: "4",
    PaymentMethod.CREDIT_NOTE: "3",
    PaymentMethod.OTHER: "4",
}
PAYMENT_CODE_CREDIT = "3"
PAYMENT_CODE_OTHER = "4"


def _flag(value: bool) -> int:
    """The OBR expresses booleans as 0/1 integers."""
    return 1 if value else 0


def _amount(value) -> float:
    """
    Render a monetary value for the wire.

    Rounded to whole francs first, so the declared figure is the one printed
    on the customer's document. JSON has no decimal type; converting a
    zero-decimal-place Decimal to float is exact for any realistic invoice.
    """
    return float(q_document(value or 0))


def payment_code_for(invoice) -> str:
    """
    Classify how the document was settled.

    A credit sale is declared as such regardless of how it is eventually
    paid, because at declaration time no payment has been received. For a
    cash sale we look at the payment actually recorded against it.
    """
    if invoice.is_credit_sale:
        return PAYMENT_CODE_CREDIT

    allocation = invoice.payment_allocations.select_related("payment").first()
    if allocation is None:
        return PAYMENT_CODE_OTHER
    return PAYMENT_CODES.get(allocation.payment.method, PAYMENT_CODE_OTHER)


def build_line(line) -> dict:
    """
    Map one invoice line.

    The declared net is the line subtotal *after* discount, matching the
    order of operations used everywhere else in the codebase: discount
    applies to the gross, tax to the discounted net.
    """
    net = Decimal(line.line_subtotal or 0) - Decimal(line.discount_amount or 0)
    tax = Decimal(line.tax_amount or 0)

    return {
        "item_code": line.product_code or "",
        "item_designation": line.description or "",
        "item_quantity": float(line.quantity or 0),
        "item_price": _amount(line.unit_price),
        "item_total_amount": _amount(net),
        "vat": _amount(tax),
        # Consumption tax and the flat-rate withholding do not apply to
        # pharmaceutical wholesale, but the fields are required by the schema.
        "item_ct": "0",
        "item_tl": "0",
        "item_price_nvat": _amount(net),
        "item_price_wvat": _amount(net + tax),
    }


def build_declaration(invoice, *, signature: str) -> dict:
    """
    Build the full declaration body for an invoice.

    Taxpayer identity is read from configuration rather than from the
    document, since it describes the issuing pharmacy and not the sale.
    Customer identity is read from the invoice's frozen snapshot, so a
    re-declaration years later reproduces exactly what was originally filed.
    """
    company = settings.COMPANY
    taxpayer = settings.OBR["TAXPAYER"]

    body = {
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date.strftime("%Y-%m-%d %H:%M:%S"),
        "invoice_type": DOCUMENT_TYPES.get(invoice.invoice_type, DOC_TYPE_NORMAL),
        "invoice_identifier": signature,
        "invoice_currency": invoice.currency,
        # --- issuing taxpayer ---
        "tp_type": str(taxpayer["TYPE"]),
        "tp_name": company["NAME"],
        "tp_TIN": company["NIF"],
        "tp_trade_number": taxpayer["TRADE_REGISTER"],
        "tp_phone_number": company["PHONE"],
        "tp_email": company["EMAIL"],
        "tp_address_province": taxpayer["PROVINCE"],
        "tp_address_commune": taxpayer["COMMUNE"],
        "tp_address_quartier": taxpayer["QUARTIER"],
        "tp_address_avenue": taxpayer["AVENUE"],
        "tp_address_rue": taxpayer["RUE"],
        "tp_address_number": taxpayer["NUMBER"],
        "tp_fiscal_center": company["TAX_CENTRE"],
        "tp_activity_sector": company["SECTOR"],
        "tp_legal_form": company["LEGAL_FORM"],
        "vat_taxpayer": _flag(taxpayer["VAT_SUBJECT"]),
        "ct_taxpayer": _flag(taxpayer["CONSUMPTION_TAX_SUBJECT"]),
        "tl_taxpayer": _flag(taxpayer["WITHHOLDING_TAX_SUBJECT"]),
        # --- customer ---
        "customer_name": invoice.customer_name,
        "customer_TIN": invoice.customer_nif or "",
        "customer_address": invoice.customer_address or "",
        "vat_customer_payer": _flag(bool(invoice.customer_nif)),
        "payment_type": payment_code_for(invoice),
        # Populated below for corrections only.
        "invoice_ref": "",
        "cn_motif": "",
        "invoice_items": [build_line(line) for line in invoice.lines.all()],
    }

    if invoice.invoice_type == InvoiceType.CREDIT_NOTE and invoice.original_invoice_id:
        original = invoice.original_invoice
        body.update(
            {
                # The OBR links a correction to the original by *its
                # signature*, not by our internal document number.
                "invoice_ref": original.fiscal_signature or original.invoice_number,
                "cn_motif": invoice.notes or "",
            }
        )

    return body


def build_cancellation(invoice, *, reason: str) -> dict:
    """
    Build the body that withdraws a previously declared document.

    A cancellation references the signature under which the document was
    originally accepted; the local status change alone does not reach the OBR.
    """
    return {
        "invoice_signature": invoice.fiscal_signature,
        "cn_motif": reason or invoice.cancellation_reason or "",
    }
