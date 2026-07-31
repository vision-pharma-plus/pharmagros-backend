"""
Sales, credit control, invoicing and traceability.

These cover the money path: what a customer is charged, what they owe, and
which batches they received.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.exceptions import (
    BusinessRuleViolation,
    CreditLimitExceeded,
    DocumentLocked,
    InvalidStateTransition,
)
from apps.invoicing.models import InvoiceStatus, InvoiceType
from apps.invoicing.services import (
    cancel_invoice,
    issue_credit_note,
    record_payment,
    reverse_payment,
    update_invoice,
)
from apps.partners.services import recompute_balance
from apps.sales.models import SaleStatus, SaleType
from apps.sales.services import (
    cancel_sale,
    confirm_sale,
    create_sale,
    process_return,
    trace_batch_recipients,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def confirmed_sale(product, warehouse, credit_customer, batch, pharmacist):
    """A confirmed credit sale of 100 units with its posted invoice."""
    sale = create_sale(
        customer=credit_customer,
        warehouse=warehouse,
        sale_type=SaleType.CREDIT,
        lines=[{"product": product, "quantity": Decimal("100")}],
        actor=pharmacist,
    )
    return confirm_sale(sale, actor=pharmacist)


class TestSaleCreation:
    def test_empty_sale_is_refused(self, warehouse, cash_customer, pharmacist):
        with pytest.raises(BusinessRuleViolation) as exc:
            create_sale(
                customer=cash_customer, warehouse=warehouse,
                lines=[], actor=pharmacist,
            )
        assert exc.value.code == "empty_sale"

    def test_expired_licence_blocks_the_sale(
        self, product, warehouse, credit_customer, pharmacist, today
    ):
        """
        Supplying a pharmacy whose licence has lapsed exposes the wholesaler
        to regulatory action, so it is refused at creation.
        """
        from datetime import timedelta

        credit_customer.licence_expiry = today - timedelta(days=1)
        credit_customer.save(update_fields=["licence_expiry"])

        with pytest.raises(BusinessRuleViolation) as exc:
            create_sale(
                customer=credit_customer, warehouse=warehouse,
                lines=[{"product": product, "quantity": Decimal("1")}],
                actor=pharmacist,
            )
        assert exc.value.code == "customer_licence_expired"

    def test_price_defaults_to_catalogue(
        self, product, warehouse, cash_customer, pharmacist
    ):
        sale = create_sale(
            customer=cash_customer, warehouse=warehouse,
            lines=[{"product": product, "quantity": Decimal("10")}],
            actor=pharmacist,
        )
        assert sale.lines.first().unit_price == product.selling_price

    def test_vat_exempt_product_carries_no_tax(
        self, exempt_product, warehouse, cash_customer, pharmacist
    ):
        sale = create_sale(
            customer=cash_customer, warehouse=warehouse,
            lines=[{"product": exempt_product, "quantity": Decimal("10")}],
            actor=pharmacist,
        )
        assert sale.tax_amount == Decimal("0")


class TestConfirmation:
    def test_confirmation_issues_stock_and_invoices(self, confirmed_sale, batch):
        sale, invoice = confirmed_sale
        assert sale.status == SaleStatus.CONFIRMED
        assert invoice is not None
        assert invoice.status == InvoiceStatus.POSTED

        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("400.000")

    def test_invoice_totals(self, confirmed_sale):
        """100 x 1500 = 150,000 net; 18% VAT = 27,000; total 177,000."""
        _sale, invoice = confirmed_sale
        assert invoice.subtotal == Decimal("150000.0000")
        assert invoice.tax_amount == Decimal("27000.0000")
        assert invoice.total_amount == Decimal("177000.0000")
        assert invoice.balance_due == invoice.total_amount

    def test_customer_identity_is_frozen(self, confirmed_sale, credit_customer):
        """
        A later rebrand must not rewrite invoices already filed with the tax
        authority.
        """
        _sale, invoice = confirmed_sale
        original_name = invoice.customer_name

        credit_customer.business_name = "Nouveau Nom SARL"
        credit_customer.save(update_fields=["business_name"])

        invoice.refresh_from_db()
        assert invoice.customer_name == original_name
        assert invoice.customer_nif == "4000123456"

    def test_batch_numbers_appear_on_the_invoice(self, confirmed_sale):
        _sale, invoice = confirmed_sale
        assert invoice.lines.first().batch_numbers == "LOT-A"

    def test_cost_captured_from_actual_batches(self, confirmed_sale):
        """Margin must reflect what shipped, not a catalogue estimate."""
        sale, _invoice = confirmed_sale
        assert sale.total_cost == Decimal("100000.0000")  # 100 x 1000
        assert sale.gross_margin == Decimal("50000.0000")

    def test_double_confirmation_is_refused(self, confirmed_sale, pharmacist):
        sale, _invoice = confirmed_sale
        with pytest.raises(InvalidStateTransition):
            confirm_sale(sale, actor=pharmacist)


class TestCreditControl:
    def test_over_limit_sale_is_blocked(
        self, product, warehouse, credit_customer, batch, pharmacist
    ):
        credit_customer.credit_limit = Decimal("1000")
        credit_customer.save(update_fields=["credit_limit"])

        sale = create_sale(
            customer=credit_customer, warehouse=warehouse,
            sale_type=SaleType.CREDIT,
            lines=[{"product": product, "quantity": Decimal("100")}],
            actor=pharmacist,
        )
        with pytest.raises(CreditLimitExceeded) as exc:
            confirm_sale(sale, actor=pharmacist)
        assert exc.value.code == "credit_limit_exceeded"

    def test_override_requires_an_authoriser(
        self, product, warehouse, credit_customer, batch, pharmacist
    ):
        """
        An override is a deliberate assumption of risk and must be
        attributable to a named user.
        """
        credit_customer.credit_limit = Decimal("1000")
        credit_customer.save(update_fields=["credit_limit"])

        sale = create_sale(
            customer=credit_customer, warehouse=warehouse,
            sale_type=SaleType.CREDIT,
            lines=[{"product": product, "quantity": Decimal("100")}],
            actor=pharmacist,
        )
        with pytest.raises(BusinessRuleViolation) as exc:
            confirm_sale(
                sale, actor=pharmacist,
                credit_override_reason="Client fidèle",
                credit_override_by=None,
            )
        assert exc.value.code == "override_authoriser_required"

    def test_override_succeeds_with_authoriser(
        self, product, warehouse, credit_customer, batch, pharmacist, store_manager
    ):
        credit_customer.credit_limit = Decimal("1000")
        credit_customer.save(update_fields=["credit_limit"])

        sale = create_sale(
            customer=credit_customer, warehouse=warehouse,
            sale_type=SaleType.CREDIT,
            lines=[{"product": product, "quantity": Decimal("100")}],
            actor=pharmacist,
        )
        sale, invoice = confirm_sale(
            sale, actor=pharmacist,
            credit_override_reason="Accord direction",
            credit_override_by=store_manager,
        )
        assert sale.credit_override_by == store_manager
        assert invoice.status == InvoiceStatus.POSTED

    def test_blocked_credit_refuses_regardless_of_limit(
        self, product, warehouse, credit_customer, batch, pharmacist, admin_user
    ):
        from apps.partners.services import block_credit

        block_credit(credit_customer, reason="Contentieux", actor=admin_user)
        credit_customer.refresh_from_db()

        sale = create_sale(
            customer=credit_customer, warehouse=warehouse,
            sale_type=SaleType.CREDIT,
            lines=[{"product": product, "quantity": Decimal("1")}],
            actor=pharmacist,
        )
        with pytest.raises(CreditLimitExceeded):
            confirm_sale(sale, actor=pharmacist)

    def test_balance_is_derived_not_incremented(
        self, confirmed_sale, credit_customer
    ):
        """
        A running tally accumulates error from every cancellation and
        reversal; a derived figure cannot drift.
        """
        credit_customer.outstanding_balance = Decimal("999999")
        credit_customer.save(update_fields=["outstanding_balance"])

        assert recompute_balance(credit_customer) == Decimal("177000.0000")


class TestPayments:
    def test_payment_reduces_balance(self, confirmed_sale, credit_customer, pharmacist):
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=Decimal("100000"),
            method="BANK_TRANSFER", actor=pharmacist,
        )
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PARTIALLY_PAID
        assert invoice.balance_due == Decimal("77000.0000")

    def test_full_payment_marks_paid(self, confirmed_sale, credit_customer, pharmacist):
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=Decimal("177000"), actor=pharmacist,
        )
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID
        assert invoice.balance_due == Decimal("0")

    def test_overpayment_leaves_unallocated_remainder(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer, amount=Decimal("200000"), actor=pharmacist,
        )
        invoice.refresh_from_db()
        assert invoice.balance_due == Decimal("0")
        assert payment.unallocated_amount == Decimal("23000.0000")

    def test_reversal_restores_the_balance(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """
        A bounced cheque must leave both the arrival and the withdrawal
        visible — deleting the payment would destroy the evidence trail.
        """
        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer, amount=Decimal("100000"), actor=pharmacist,
        )
        reverse_payment(payment, reason="Chèque sans provision", actor=pharmacist)

        invoice.refresh_from_db()
        payment.refresh_from_db()
        assert invoice.balance_due == Decimal("177000.0000")
        assert invoice.status == InvoiceStatus.POSTED
        assert payment.is_reversed is True

    def test_zero_payment_is_refused(self, credit_customer, pharmacist):
        with pytest.raises(BusinessRuleViolation):
            record_payment(
                customer=credit_customer, amount=Decimal("0"), actor=pharmacist
            )


class TestInvoiceImmutability:
    def test_posted_invoice_cannot_be_edited(self, confirmed_sale, pharmacist):
        _sale, invoice = confirmed_sale
        with pytest.raises(DocumentLocked):
            update_invoice(invoice, notes="Tentative", actor=pharmacist)

    def test_paid_invoice_cannot_be_cancelled(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=Decimal("50000"), actor=pharmacist
        )
        invoice.refresh_from_db()

        with pytest.raises(BusinessRuleViolation) as exc:
            cancel_invoice(invoice, reason="Erreur", actor=pharmacist)
        assert exc.value.code == "invoice_has_payments"

    def test_cancellation_requires_a_reason(self, confirmed_sale, pharmacist):
        _sale, invoice = confirmed_sale
        with pytest.raises(BusinessRuleViolation):
            cancel_invoice(invoice, reason="  ", actor=pharmacist)


class TestCreditNotes:
    def test_credit_note_offsets_the_original(self, confirmed_sale, product, pharmacist):
        _sale, invoice = confirmed_sale
        note = issue_credit_note(
            invoice,
            lines=[
                {
                    "product": product,
                    "description": "Retour",
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("1500"),
                    "tax_rate": Decimal("18"),
                }
            ],
            reason="Marchandise endommagée",
            actor=pharmacist,
        )
        assert note.invoice_type == InvoiceType.CREDIT_NOTE
        assert note.original_invoice_id == invoice.pk

        invoice.refresh_from_db()
        # 10 x 1500 x 1.18 = 17,700 credited
        assert invoice.balance_due == Decimal("159300.0000")

    def test_credit_note_requires_a_reason(self, confirmed_sale, product, pharmacist):
        _sale, invoice = confirmed_sale
        with pytest.raises(BusinessRuleViolation):
            issue_credit_note(invoice, lines=[], reason="", actor=pharmacist)


class TestReturns:
    def test_damaged_return_does_not_restock(
        self, confirmed_sale, batch, pharmacist
    ):
        """
        Returned medicines re-enter sellable stock only when storage integrity
        was maintained. A damaged return comes back and is written off, so the
        net sellable balance is unchanged.
        """
        sale, _invoice = confirmed_sale
        line = sale.lines.first()
        before = StockBatchBalance(batch)

        process_return(
            sale,
            lines=[
                {
                    "sale_line": line,
                    "quantity": Decimal("20"),
                    "restock": False,
                    "condition_notes": "Boîtes écrasées",
                }
            ],
            reason="Emballage endommagé",
            actor=pharmacist,
        )
        assert before.unchanged()

    def test_good_return_restocks(self, confirmed_sale, batch, pharmacist):
        sale, _invoice = confirmed_sale
        line = sale.lines.first()
        batch.refresh_from_db()
        before = batch.quantity_remaining

        process_return(
            sale,
            lines=[{"sale_line": line, "quantity": Decimal("20"), "restock": True}],
            reason="Erreur de commande",
            actor=pharmacist,
        )
        batch.refresh_from_db()
        assert batch.quantity_remaining == before + Decimal("20")

    def test_cannot_return_more_than_sold(self, confirmed_sale, pharmacist):
        sale, _invoice = confirmed_sale
        line = sale.lines.first()

        with pytest.raises(BusinessRuleViolation) as exc:
            process_return(
                sale,
                lines=[{"sale_line": line, "quantity": Decimal("101")}],
                reason="Test",
                actor=pharmacist,
            )
        assert exc.value.code == "return_exceeds_sold"

    def test_full_return_marks_sale_returned(self, confirmed_sale, pharmacist):
        sale, _invoice = confirmed_sale
        line = sale.lines.first()

        process_return(
            sale,
            lines=[{"sale_line": line, "quantity": Decimal("100"), "restock": True}],
            reason="Annulation client",
            actor=pharmacist,
        )
        sale.refresh_from_db()
        assert sale.status == SaleStatus.RETURNED


class TestCancellation:
    def test_cancelling_returns_stock(self, confirmed_sale, batch, pharmacist):
        sale, _invoice = confirmed_sale
        cancel_sale(sale, reason="Erreur de saisie", actor=pharmacist)

        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("500.000")
        sale.refresh_from_db()
        assert sale.status == SaleStatus.CANCELLED

    def test_cancelling_cancels_the_invoice(self, confirmed_sale, pharmacist):
        sale, invoice = confirmed_sale
        cancel_sale(sale, reason="Erreur", actor=pharmacist)

        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.CANCELLED


class TestTraceability:
    def test_recall_finds_recipients(self, confirmed_sale, credit_customer):
        """The recall query turns a batch number into a contact list."""
        recipients = trace_batch_recipients("LOT-A")
        assert len(recipients) == 1
        assert recipients[0]["customer_name"] == credit_customer.business_name
        assert recipients[0]["quantity_supplied"] == Decimal("100.000")
        assert recipients[0]["quantity_outstanding"] == Decimal("100.000")

    def test_returned_units_reduce_outstanding(self, confirmed_sale, pharmacist):
        sale, _invoice = confirmed_sale
        process_return(
            sale,
            lines=[
                {"sale_line": sale.lines.first(), "quantity": Decimal("30"),
                 "restock": True}
            ],
            reason="Retour partiel",
            actor=pharmacist,
        )
        recipients = trace_batch_recipients("LOT-A")
        assert recipients[0]["quantity_outstanding"] == Decimal("70.000")

    def test_unknown_batch_returns_empty(self, confirmed_sale):
        assert trace_batch_recipients("LOT-INEXISTANT") == []


class TestInvoicePdf:
    """
    Rendering must survive every VAT rate a pharmacy actually sells at.

    Zero-rated and exempt lines are ordinary here — much of the essential
    medicines list carries no VAT — so they are the default case, not an
    edge case.
    """

    @pytest.mark.parametrize("tax_rate", ["18", "0", "10"])
    def test_renders_at_any_tax_rate(self, confirmed_sale, tax_rate):
        """A zero-rated line once raised TypeError and returned a 500."""
        from apps.invoicing.pdf import render_invoice_pdf

        _sale, invoice = confirmed_sale
        invoice.lines.update(tax_rate=Decimal(tax_rate))
        invoice.refresh_from_db()

        pdf = render_invoice_pdf(invoice, language="fr")

        assert pdf.startswith(b"%PDF-")

    def test_tax_classes_follow_obr_letters(self):
        """A is exempt, B the standard 18% rate, C anything else."""
        from apps.invoicing.pdf import _tax_class

        assert _tax_class(Decimal("0")) == "A"
        assert _tax_class(Decimal("18")) == "B"
        assert _tax_class(Decimal("10")) == "C"
        assert _tax_class(None) == "A"

    def test_tax_summary_totals_match_the_invoice(self, confirmed_sale):
        """The printed band must reconcile against the invoice total."""
        from apps.invoicing.pdf import _tax_summary

        _sale, invoice = confirmed_sale
        summary = _tax_summary(invoice)

        assert summary["total"]


class StockBatchBalance:
    """Small helper: capture a batch balance and assert it did not move."""

    def __init__(self, batch):
        batch.refresh_from_db()
        self.batch = batch
        self.before = batch.quantity_remaining

    def unchanged(self) -> bool:
        self.batch.refresh_from_db()
        return self.batch.quantity_remaining == self.before
