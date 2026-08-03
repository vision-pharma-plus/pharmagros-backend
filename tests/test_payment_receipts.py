"""
Automatic payment receipts.

When an invoice is settled in full the customer expects a document confirming
it. Leaving that to a manual step means the receipts nobody remembers to issue
simply never exist, so one is raised automatically the moment the balance
reaches zero.

The rules pinned here: exactly one receipt per invoice however many payments
contributed, nothing issued while a balance remains, and a receipt that stops
being true — because the payment was reversed — is cancelled rather than
deleted, since the customer was already handed that document.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.invoicing.models import InvoiceStatus, PaymentReceipt
from apps.invoicing.services import record_payment, reverse_payment
from tests.test_sales_invoicing import INVOICE_TOTAL, confirmed_sale  # noqa: F401

pytestmark = pytest.mark.django_db


def _receipt_for(invoice):
    return PaymentReceipt.objects.filter(invoice=invoice).first()


class TestIssuedOnSettlement:
    def test_no_receipt_before_payment(self, confirmed_sale):
        _sale, invoice = confirmed_sale
        assert _receipt_for(invoice) is None

    def test_no_receipt_while_a_balance_remains(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """A part payment is not settlement, so it earns no acknowledgement."""
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer,
            amount=INVOICE_TOTAL - Decimal("1000"),
            method="CASH",
            actor=pharmacist,
        )

        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PARTIALLY_PAID
        assert _receipt_for(invoice) is None

    def test_receipt_issued_when_paid_in_full(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer,
            amount=INVOICE_TOTAL,
            method="BANK_TRANSFER",
            bank_reference="TRF-1",
            actor=pharmacist,
        )

        invoice.refresh_from_db()
        receipt = _receipt_for(invoice)

        assert invoice.status == InvoiceStatus.PAID
        assert receipt is not None
        assert receipt.receipt_number
        assert receipt.invoice_id == invoice.id
        assert receipt.settling_payment_id == payment.id
        # Amounts are read from the invoice, never copied.
        assert receipt.amount_paid == invoice.paid_amount
        assert receipt.invoice_total == invoice.total_amount

    def test_customer_identity_is_snapshotted(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """The receipt must reproduce who paid, as at the day it was issued."""
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=INVOICE_TOTAL, method="CASH", actor=pharmacist,
        )

        receipt = _receipt_for(invoice)
        original_name = receipt.customer_name
        assert original_name

        credit_customer.business_name = "Renamed After The Fact SARL"
        credit_customer.save(update_fields=["business_name"])

        receipt.refresh_from_db()
        assert receipt.customer_name == original_name

    def test_settlement_in_two_payments_yields_one_receipt(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """
        The receipt acknowledges the invoice, not each payment.

        Two receipts for one invoice would double-count in anything that reads
        them, and would leave the customer holding two documents for one debt.
        """
        _sale, invoice = confirmed_sale
        half = (INVOICE_TOTAL / 2).quantize(Decimal("1"))

        record_payment(
            customer=credit_customer, amount=half, method="CASH", actor=pharmacist,
        )
        record_payment(
            customer=credit_customer,
            amount=INVOICE_TOTAL - half,
            method="CASH",
            actor=pharmacist,
        )

        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID
        assert PaymentReceipt.objects.filter(invoice=invoice).count() == 1


def test_credit_note_settlement_also_issues_a_receipt(
    confirmed_sale, credit_customer, pharmacist
):
    """
    An invoice cleared by a credit note is settled too.

    It routes through the same `_apply_to_invoice`, so it earns the same
    acknowledgement — an invoice reading PAID with no receipt behind it would
    be an inconsistency the customer notices before anyone else does.
    """
    from apps.invoicing.services import issue_credit_note

    _sale, invoice = confirmed_sale
    issue_credit_note(
        invoice,
        lines=[
            {
                "description": "Goods returned",
                "quantity": Decimal("1"),
                "unit_price": invoice.total_amount,
                "tax_rate": Decimal("0"),
            }
        ],
        reason="Goods returned in full",
        actor=pharmacist,
    )

    invoice.refresh_from_db()
    # Asserted rather than assumed: a conditional check here would pass
    # silently if the note stopped clearing the balance, which is exactly the
    # regression this test exists to catch.
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.balance_due == Decimal("0")
    assert _receipt_for(invoice) is not None, (
        "an invoice settled by credit note has no receipt"
    )


class TestReversal:
    def test_reversal_cancels_the_receipt(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """A reopened invoice must not keep a receipt claiming it is settled."""
        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer, amount=INVOICE_TOTAL, method="CHEQUE", actor=pharmacist,
        )
        receipt = _receipt_for(invoice)
        assert receipt is not None and not receipt.is_cancelled

        reverse_payment(payment, reason="Cheque bounced", actor=pharmacist)

        receipt.refresh_from_db()
        invoice.refresh_from_db()
        assert invoice.status != InvoiceStatus.PAID
        assert receipt.is_cancelled
        assert "bounced" in receipt.cancellation_reason.lower()

    def test_receipt_is_kept_not_deleted(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """The document was handed over, so its number stays consumed."""
        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer, amount=INVOICE_TOTAL, method="CHEQUE", actor=pharmacist,
        )
        number = _receipt_for(invoice).receipt_number

        reverse_payment(payment, reason="Erroneous entry", actor=pharmacist)

        assert PaymentReceipt.objects.filter(receipt_number=number).exists()

    def test_repayment_revives_the_same_receipt(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """
        Paying again must not mint a second number for the same invoice.

        The customer already holds the first receipt; reviving it keeps that
        document valid rather than superseding it with one they never saw.
        """
        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer, amount=INVOICE_TOTAL, method="CHEQUE", actor=pharmacist,
        )
        number = _receipt_for(invoice).receipt_number
        reverse_payment(payment, reason="Cheque bounced", actor=pharmacist)

        record_payment(
            customer=credit_customer, amount=INVOICE_TOTAL, method="CASH", actor=pharmacist,
        )

        invoice.refresh_from_db()
        receipts = PaymentReceipt.objects.filter(invoice=invoice)
        assert invoice.status == InvoiceStatus.PAID
        assert receipts.count() == 1
        assert receipts.first().receipt_number == number
        assert not receipts.first().is_cancelled


class TestApi:
    def test_receipt_is_linked_from_the_invoice(
        self, auth_client, confirmed_sale, credit_customer, pharmacist
    ):
        """The invoice detail page can reach the receipt without a search."""
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=INVOICE_TOTAL, method="CASH", actor=pharmacist,
        )

        response = auth_client(pharmacist).get(reverse("invoice-detail", args=[invoice.id]))

        assert response.status_code == 200
        assert response.data["payment_receipt_number"] == _receipt_for(invoice).receipt_number

    def test_receipt_is_listed(
        self, auth_client, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=INVOICE_TOTAL, method="CASH", actor=pharmacist,
        )

        response = auth_client(pharmacist).get(reverse("payment-receipt-list"))

        assert response.status_code == 200
        numbers = [row["receipt_number"] for row in response.data["results"]]
        assert _receipt_for(invoice).receipt_number in numbers

    def test_receipts_cannot_be_created_by_hand(self, auth_client, pharmacist):
        """
        A receipt must correspond to money actually received.

        The viewset exposes no create route, so there is no way to mint a
        receipt for a payment nobody made. The refusal is a 403 rather than a
        405 because the permission map has no entry for `create` and denies it
        before DRF gets as far as reporting the method unsupported; either way
        no receipt exists afterwards, which is the guarantee that matters.
        """
        before = PaymentReceipt.objects.count()

        response = auth_client(pharmacist).post(reverse("payment-receipt-list"), {})

        assert response.status_code in (403, 405)
        assert PaymentReceipt.objects.count() == before
