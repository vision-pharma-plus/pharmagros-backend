"""
Cash sales, credit sales, and the documents that close them.

The rule under test throughout: a sale posts stock, VAT and revenue exactly
once, no matter how many documents describe it. A cash sale closes with a
receipt; a credit sale closes with an invoice; a cash sale that later needs an
invoice gets a second *document*, never a second sale.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.exceptions import BusinessRuleViolation
from apps.inventory.models import MovementType, StockMovement
from apps.invoicing.models import Invoice, InvoiceStatus
from apps.sales.models import SaleStatus, SaleType
from apps.sales.services import (
    cancel_sale,
    confirm_sale,
    create_sale,
    invoice_for_receipt,
    process_return,
)

pytestmark = pytest.mark.django_db


def _issue_movements(sale):
    """Every stock issue posted against a sale."""
    return StockMovement.objects.filter(
        source_type="sales.Sale",
        source_id=str(sale.pk),
        movement_type=MovementType.ISSUE,
    )


@pytest.fixture
def cash_sale(product, warehouse, cash_customer, batch, pharmacist):
    """A confirmed cash sale of 10 units, with its receipt."""
    sale = create_sale(
        customer=cash_customer,
        warehouse=warehouse,
        sale_type=SaleType.CASH,
        lines=[{"product": product, "quantity": Decimal("10")}],
        actor=pharmacist,
    )
    return confirm_sale(sale, actor=pharmacist)


@pytest.fixture
def credit_sale(product, warehouse, credit_customer, batch, pharmacist):
    """A confirmed credit sale of 10 units, with its posted invoice."""
    sale = create_sale(
        customer=credit_customer,
        warehouse=warehouse,
        sale_type=SaleType.CREDIT,
        lines=[{"product": product, "quantity": Decimal("10")}],
        actor=pharmacist,
    )
    return confirm_sale(sale, actor=pharmacist)


class TestCashSaleIssuesAReceipt:
    def test_a_receipt_is_issued(self, cash_sale):
        _sale, _invoice, receipt = cash_sale
        assert receipt is not None
        assert receipt.receipt_number.startswith("RV-")

    def test_no_invoice_is_created(self, cash_sale):
        """The point of the whole feature: a counter sale is not invoiced."""
        sale, invoice, _receipt = cash_sale
        assert invoice is None
        assert getattr(sale, "invoice", None) is None
        assert not Invoice.objects.filter(sale=sale).exists()

    def test_the_sale_is_confirmed_and_settled(self, cash_sale):
        sale, _invoice, receipt = cash_sale
        assert sale.status == SaleStatus.CONFIRMED
        assert receipt.total_amount == sale.total_amount

    def test_receipt_amounts_track_the_sale(self, cash_sale):
        """Amounts are read from the sale, never copied, so they cannot drift."""
        sale, _invoice, receipt = cash_sale
        assert receipt.subtotal == sale.subtotal
        assert receipt.tax_amount == sale.tax_amount
        assert receipt.total_amount == sale.total_amount

    def test_customer_identity_is_frozen(self, cash_sale, cash_customer):
        _sale, _invoice, receipt = cash_sale
        cash_customer.business_name = "Renamed Pharmacy"
        cash_customer.save(update_fields=["business_name"])

        receipt.refresh_from_db()
        assert receipt.customer_name == "Pharmacie Comptant"

    def test_stock_is_issued_once(self, cash_sale, batch):
        sale, _invoice, _receipt = cash_sale
        assert _issue_movements(sale).count() == 1
        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("490")

    def test_a_cash_sale_adds_nothing_to_the_customer_balance(
        self, cash_sale, cash_customer
    ):
        """
        The money is already in. A cash sale that moved the balance would
        invent a debt for a customer who owes nothing.
        """
        cash_customer.refresh_from_db()
        assert cash_customer.outstanding_balance == Decimal("0")

    def test_change_is_computed_from_the_tender(
        self, product, warehouse, cash_customer, batch, pharmacist
    ):
        sale = create_sale(
            customer=cash_customer,
            warehouse=warehouse,
            sale_type=SaleType.CASH,
            lines=[{"product": product, "quantity": Decimal("1")}],
            actor=pharmacist,
        )
        sale, _invoice, receipt = confirm_sale(
            sale, actor=pharmacist, amount_tendered=Decimal("5000"),
        )
        assert receipt.amount_tendered == Decimal("5000")
        assert receipt.change_given == Decimal("5000") - sale.total_amount

    def test_tender_below_the_total_is_refused(
        self, product, warehouse, cash_customer, batch, pharmacist
    ):
        sale = create_sale(
            customer=cash_customer,
            warehouse=warehouse,
            sale_type=SaleType.CASH,
            lines=[{"product": product, "quantity": Decimal("10")}],
            actor=pharmacist,
        )
        with pytest.raises(BusinessRuleViolation) as exc:
            confirm_sale(sale, actor=pharmacist, amount_tendered=Decimal("1"))
        assert exc.value.code == "insufficient_tender"

    def test_tender_defaults_to_the_exact_total(self, cash_sale):
        """No change is due when nothing was entered — the common counter case."""
        sale, _invoice, receipt = cash_sale
        assert receipt.amount_tendered == sale.total_amount
        assert receipt.change_given == Decimal("0")


class TestCreditSaleIssuesAnInvoice:
    def test_an_invoice_is_posted(self, credit_sale):
        _sale, invoice, _receipt = credit_sale
        assert invoice is not None
        assert invoice.status == InvoiceStatus.POSTED
        assert invoice.is_credit_sale

    def test_no_receipt_is_issued(self, credit_sale):
        """Nothing has been paid yet, so there is nothing to receipt."""
        sale, _invoice, receipt = credit_sale
        assert receipt is None
        assert getattr(sale, "receipt", None) is None

    def test_the_invoice_stays_open(self, credit_sale):
        _sale, invoice, _receipt = credit_sale
        assert invoice.balance_due == invoice.total_amount
        assert invoice.paid_amount == Decimal("0")

    def test_it_raises_the_customer_balance(self, credit_sale, credit_customer):
        _sale, invoice, _receipt = credit_sale
        credit_customer.refresh_from_db()
        assert credit_customer.outstanding_balance == invoice.total_amount

    def test_it_carries_a_due_date(self, credit_sale):
        _sale, invoice, _receipt = credit_sale
        assert invoice.due_date is not None


class TestInvoiceForACashSale:
    """
    A customer or tax rule demands an invoice for a sale already paid at the
    counter. The invoice is a second document, never a second sale.
    """

    def test_an_invoice_is_raised_against_the_receipt(self, cash_sale, pharmacist):
        sale, _invoice, receipt = cash_sale
        invoice = invoice_for_receipt(receipt, actor=pharmacist)

        assert invoice.source_receipt_id == receipt.pk
        assert invoice.sale_id == sale.pk
        assert invoice.reference == receipt.receipt_number

    def test_the_receipt_survives(self, cash_sale, pharmacist):
        """Conversion does not consume the receipt: it is still proof of payment."""
        _sale, _invoice, receipt = cash_sale
        invoice_for_receipt(receipt, actor=pharmacist)

        receipt.refresh_from_db()
        assert not receipt.is_cancelled
        assert receipt.has_invoice

    def test_the_invoice_is_already_settled(self, cash_sale, pharmacist):
        """
        The money arrived at the counter. An open invoice here would misstate
        both the customer's balance and the receivables ledger.
        """
        _sale, _invoice, receipt = cash_sale
        invoice = invoice_for_receipt(receipt, actor=pharmacist)

        assert invoice.status == InvoiceStatus.PAID
        assert invoice.balance_due == Decimal("0")
        assert invoice.paid_amount == invoice.total_amount
        assert not invoice.is_credit_sale

    def test_it_does_not_move_stock_again(self, cash_sale, batch, pharmacist):
        sale, _invoice, receipt = cash_sale
        before = _issue_movements(sale).count()
        batch.refresh_from_db()
        on_hand_before = batch.quantity_remaining

        invoice_for_receipt(receipt, actor=pharmacist)

        assert _issue_movements(sale).count() == before
        batch.refresh_from_db()
        assert batch.quantity_remaining == on_hand_before

    def test_it_does_not_charge_tax_or_revenue_twice(self, cash_sale, pharmacist):
        """The invoice restates the sale's own figures; it does not add to them."""
        sale, _invoice, receipt = cash_sale
        invoice = invoice_for_receipt(receipt, actor=pharmacist)

        assert invoice.total_amount == sale.total_amount
        assert invoice.tax_amount == sale.tax_amount
        assert invoice.subtotal == sale.subtotal

    def test_it_does_not_raise_the_customer_balance(
        self, cash_sale, cash_customer, pharmacist
    ):
        _sale, _invoice, receipt = cash_sale
        invoice_for_receipt(receipt, actor=pharmacist)

        cash_customer.refresh_from_db()
        assert cash_customer.outstanding_balance == Decimal("0")

    def test_the_invoice_lines_match_the_sale(self, cash_sale, pharmacist):
        sale, _invoice, receipt = cash_sale
        invoice = invoice_for_receipt(receipt, actor=pharmacist)

        assert invoice.lines.count() == sale.lines.count()
        sale_line = sale.lines.first()
        invoice_line = invoice.lines.first()
        assert invoice_line.quantity == sale_line.quantity
        assert invoice_line.unit_price == sale_line.unit_price
        assert invoice_line.line_total == sale_line.line_total

    def test_batch_traceability_is_carried_over(self, cash_sale, pharmacist):
        """A recall must reach the customer through either document."""
        _sale, _invoice, receipt = cash_sale
        invoice = invoice_for_receipt(receipt, actor=pharmacist)

        assert "LOT-A" in invoice.lines.first().batch_numbers

    def test_the_invoice_dates_to_the_sale_not_today(self, cash_sale, pharmacist):
        """
        A fiscal document must fall in the period the sale occurred in,
        otherwise its VAT lands in the wrong return.
        """
        sale, _invoice, receipt = cash_sale
        invoice = invoice_for_receipt(receipt, actor=pharmacist)

        assert invoice.invoice_date == sale.sale_date

    def test_invoicing_twice_is_refused(self, cash_sale, pharmacist):
        """One sale, one invoice. The second attempt names the first."""
        _sale, _invoice, receipt = cash_sale
        first = invoice_for_receipt(receipt, actor=pharmacist)

        with pytest.raises(BusinessRuleViolation) as exc:
            invoice_for_receipt(receipt, actor=pharmacist)

        assert exc.value.code == "already_invoiced"
        assert first.invoice_number in str(exc.value)

    def test_only_one_invoice_row_exists_for_the_sale(self, cash_sale, pharmacist):
        sale, _invoice, receipt = cash_sale
        invoice_for_receipt(receipt, actor=pharmacist)

        assert Invoice.objects.filter(sale=sale).count() == 1

    def test_a_cancelled_receipt_cannot_be_invoiced(self, cash_sale, pharmacist):
        sale, _invoice, receipt = cash_sale
        cancel_sale(sale, reason="Erreur de saisie", actor=pharmacist)
        receipt.refresh_from_db()

        with pytest.raises(BusinessRuleViolation) as exc:
            invoice_for_receipt(receipt, actor=pharmacist)
        assert exc.value.code == "receipt_cancelled"


class TestForcingAnInvoiceAtCheckout:
    """
    Some tax regimes require an invoice on every sale. The sale is still
    receipted, and the two documents are linked rather than standing apart.
    """

    @pytest.fixture
    def forced(self, product, warehouse, cash_customer, batch, pharmacist):
        sale = create_sale(
            customer=cash_customer,
            warehouse=warehouse,
            sale_type=SaleType.CASH,
            lines=[{"product": product, "quantity": Decimal("10")}],
            actor=pharmacist,
        )
        return confirm_sale(sale, actor=pharmacist, generate_invoice=True)

    def test_both_documents_are_produced(self, forced):
        _sale, invoice, receipt = forced
        assert invoice is not None
        assert receipt is not None

    def test_they_are_linked(self, forced):
        _sale, invoice, receipt = forced
        invoice.refresh_from_db()
        assert invoice.source_receipt_id == receipt.pk

    def test_stock_still_moves_only_once(self, forced, batch):
        sale, _invoice, _receipt = forced
        assert _issue_movements(sale).count() == 1
        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("490")

    def test_the_totals_agree(self, forced):
        sale, invoice, receipt = forced
        assert invoice.total_amount == sale.total_amount == receipt.total_amount


class TestCancellation:
    def test_cancelling_a_cash_sale_voids_the_receipt(self, cash_sale, pharmacist):
        sale, _invoice, receipt = cash_sale
        cancel_sale(sale, reason="Client parti", actor=pharmacist)

        receipt.refresh_from_db()
        assert receipt.is_cancelled
        assert receipt.cancellation_reason == "Client parti"

    def test_the_receipt_row_is_retained(self, cash_sale, pharmacist):
        """It was handed to a customer, so it must stay findable."""
        sale, _invoice, receipt = cash_sale
        cancel_sale(sale, reason="Client parti", actor=pharmacist)

        from apps.sales.models import SalesReceipt

        assert SalesReceipt.objects.filter(pk=receipt.pk).exists()

    def test_cancelling_returns_the_stock(self, cash_sale, batch, pharmacist):
        sale, _invoice, _receipt = cash_sale
        cancel_sale(sale, reason="Client parti", actor=pharmacist)

        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("500")

    def test_cancelling_also_cancels_an_invoice_raised_from_the_receipt(
        self, cash_sale, pharmacist
    ):
        """
        The invoice was settled from the receipt rather than a Payment, so
        cancellation has to release that settlement before voiding it.
        """
        sale, _invoice, receipt = cash_sale
        invoice = invoice_for_receipt(receipt, actor=pharmacist)

        cancel_sale(sale, reason="Erreur", actor=pharmacist)

        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.CANCELLED


class TestReturnsAgainstACashSale:
    def test_a_return_restocks_without_a_credit_note(
        self, cash_sale, batch, pharmacist
    ):
        """
        A receipted cash sale has no invoice, so there is nothing to credit.
        The refund is settled at the counter; the stock movement and the
        SaleReturn record are the whole trail.
        """
        sale, _invoice, _receipt = cash_sale
        sale_line = sale.lines.first()

        sale_return = process_return(
            sale,
            lines=[
                {
                    "sale_line": sale_line,
                    "quantity": Decimal("4"),
                    "restock": True,
                }
            ],
            reason="Client a changé d'avis",
            actor=pharmacist,
        )

        assert sale_return.credit_note is None
        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("494")

    def test_a_return_after_invoicing_raises_a_credit_note(
        self, cash_sale, pharmacist
    ):
        """Once an invoice exists, the lawful correction is a credit note."""
        sale, _invoice, receipt = cash_sale
        invoice_for_receipt(receipt, actor=pharmacist)
        sale_line = sale.lines.first()

        sale_return = process_return(
            sale,
            lines=[
                {
                    "sale_line": sale_line,
                    "quantity": Decimal("2"),
                    "restock": True,
                }
            ],
            reason="Produit refusé",
            actor=pharmacist,
        )

        assert sale_return.credit_note is not None
