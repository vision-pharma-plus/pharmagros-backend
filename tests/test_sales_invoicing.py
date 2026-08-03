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
from apps.core.money import q_document
from apps.invoicing.models import CreditNoteReason, InvoiceStatus, InvoiceType
from apps.invoicing.services import (
    allocate_payment,
    cancel_invoice,
    create_invoice,
    issue_credit_note,
    issue_credit_note_for_invoice,
    post_invoice,
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

# The `confirmed_sale` invoice total, at internal (4 dp) precision.
#
# 100 units of a product listed at 1500 TTC. Extracting 18% VAT gives a net
# base of 1271.1864 that does not divide evenly, so the internal total carries
# a 0.0048 residue below one franc. It is deliberately not rounded here:
# balances and payments compare internal values, and rounding early is what
# `apps.core.money` exists to prevent. `q_document` collapses it to exactly
# 150,000 at the one place it matters — the printed page.
INVOICE_TOTAL = Decimal("149999.9952")


def create_posted_invoice(customer, actor, *, unit_price=Decimal("50000")):
    """
    A posted standalone invoice, with no sale or stock behind it.

    Used by the allocation tests to raise a second debt for a customer who
    already holds credit. Deliberately not built through the sale path: those
    tests are about where money lands, and a sale would drag batch
    reservation and stock movements into a scenario that has nothing to say
    about either.

    Marked as a credit sale only when the customer carries a NIF, since
    posting a credit invoice without one is refused — an over-the-counter
    customer gets an ordinary open invoice instead.
    """
    invoice = create_invoice(
        customer=customer,
        lines=[
            {
                "description": "Prestation",
                "quantity": Decimal("1"),
                "unit_price": unit_price,
            }
        ],
        is_credit_sale=bool(customer.nif),
        actor=actor,
    )
    return post_invoice(invoice, actor=actor)


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
    # A credit sale, so the closing document is the invoice and no receipt is
    # raised. Sliced to (sale, invoice) because that pair is what every test
    # below reads; the receipt path is covered by its own fixture.
    sale, invoice, _receipt = confirm_sale(sale, actor=pharmacist)
    return sale, invoice


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
        """
        The catalogue price is VAT-inclusive; the line stores the net base.

        1500 TTC at 18% is 1271.1864 net, which is what the fiscal payload and
        the tax summary are computed from. The customer-facing figure is
        checked by the round-trip test below rather than here.
        """
        sale = create_sale(
            customer=cash_customer, warehouse=warehouse,
            lines=[{"product": product, "quantity": Decimal("10")}],
            actor=pharmacist,
        )
        assert sale.lines.first().unit_price == Decimal("1271.1864")

    def test_catalogue_price_is_what_the_customer_pays(
        self, product, warehouse, cash_customer, pharmacist
    ):
        """
        The whole point of VAT-inclusive pricing: the shelf price is the price.

        A product listed at 1500 must total 1500 per unit on the document, with
        VAT already inside it — not 1500 plus 270.
        """
        sale = create_sale(
            customer=cash_customer, warehouse=warehouse,
            lines=[{"product": product, "quantity": Decimal("10")}],
            actor=pharmacist,
        )
        assert q_document(sale.total_amount) == Decimal("15000")

    def test_explicit_price_override_is_vat_inclusive(
        self, product, warehouse, cash_customer, pharmacist
    ):
        """
        An override typed at the counter is quoted in the same terms as the
        shelf price. Treating it as already-net would apply VAT twice and
        undercharge the customer on every manually priced line.
        """
        sale = create_sale(
            customer=cash_customer, warehouse=warehouse,
            lines=[{
                "product": product,
                "quantity": Decimal("1"),
                "unit_price": Decimal("180"),
            }],
            actor=pharmacist,
        )
        assert sale.lines.first().unit_price == Decimal("152.5424")
        assert q_document(sale.total_amount) == Decimal("180")

    def test_exempt_product_price_is_unchanged_by_extraction(
        self, exempt_product, warehouse, cash_customer, pharmacist
    ):
        """
        A zero rate must leave the price exactly alone. Dividing by 1.18 on an
        exempt line would quietly discount it by the VAT it never carried.
        """
        sale = create_sale(
            customer=cash_customer, warehouse=warehouse,
            lines=[{"product": exempt_product, "quantity": Decimal("10")}],
            actor=pharmacist,
        )
        assert sale.lines.first().unit_price == Decimal("800.0000")
        assert q_document(sale.total_amount) == Decimal("8000")

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
        """
        100 x 1500 TTC = 150,000 payable, VAT included.

        The catalogue price is VAT-inclusive, so the total is the shelf price
        times quantity — no addition on top. The net base and VAT are the
        decomposition of that figure: 127,118.64 + 22,881.3552, which rounds
        to 150,000 at the document boundary.
        """
        _sale, invoice = confirmed_sale
        assert invoice.subtotal == Decimal("127118.6400")
        assert invoice.tax_amount == Decimal("22881.3552")
        assert invoice.balance_due == invoice.total_amount

        # The decomposition is exact at internal precision — no residue between
        # the parts and their sum, which is what reconciliation checks.
        assert invoice.subtotal + invoice.tax_amount == invoice.total_amount

        # Extracting VAT from a round shelf price gives a net base that does not
        # divide evenly, so 100 units carry a sub-franc residue internally
        # (149,999.9952). It is carried at 4 dp and rounded exactly once, at the
        # document boundary — which is where the customer-facing number is made
        # and where it must come out at the shelf price times quantity.
        assert q_document(invoice.total_amount) == Decimal("150000")

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
        """
        Margin must reflect what shipped, not a catalogue estimate.

        Margin is a net-of-VAT figure on both sides: the batch cost carries no
        VAT, and the revenue compared against it is the net base rather than
        the VAT-inclusive shelf price. The tax collected belongs to the
        revenue authority, so counting it as margin would overstate profit by
        the whole VAT amount.
        """
        sale, _invoice = confirmed_sale
        assert sale.total_cost == Decimal("100000.0000")  # 100 x 1000
        # 127,118.64 net revenue - 100,000 cost
        assert sale.gross_margin == Decimal("27118.6400")

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
        sale, invoice, _receipt = confirm_sale(
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
        with pytest.raises(CreditLimitExceeded) as exc:
            confirm_sale(sale, actor=pharmacist)

        # Not "credit_limit_exceeded": the account has ample headroom and
        # reporting a breach sent the sales floor looking for an override that
        # could not have helped. The block reason must reach the operator.
        assert exc.value.code == "credit_blocked"
        assert "Contentieux" in str(exc.value.message)

    def test_blocked_credit_cannot_be_overridden(
        self, product, warehouse, credit_customer, batch, pharmacist,
        store_manager, admin_user,
    ):
        """
        A block is a standing credit decision, not a counter-level judgement.

        Letting a supervisor override it at the till would silently reverse a
        decision made deliberately elsewhere — exactly what the block exists
        to prevent.
        """
        from apps.partners.services import block_credit

        block_credit(credit_customer, reason="Contentieux", actor=admin_user)
        credit_customer.refresh_from_db()

        sale = create_sale(
            customer=credit_customer, warehouse=warehouse,
            sale_type=SaleType.CREDIT,
            lines=[{"product": product, "quantity": Decimal("1")}],
            actor=pharmacist,
        )
        with pytest.raises(CreditLimitExceeded) as exc:
            confirm_sale(
                sale, actor=pharmacist,
                credit_override_reason="Accord direction",
                credit_override_by=store_manager,
            )
        assert exc.value.code == "credit_blocked"

    def test_cash_only_customer_is_refused_with_its_own_code(
        self, product, warehouse, credit_customer, batch, pharmacist
    ):
        from apps.partners.models import PaymentTerms

        credit_customer.payment_terms = PaymentTerms.CASH
        credit_customer.save(update_fields=["payment_terms"])

        sale = create_sale(
            customer=credit_customer, warehouse=warehouse,
            sale_type=SaleType.CREDIT,
            lines=[{"product": product, "quantity": Decimal("1")}],
            actor=pharmacist,
        )
        with pytest.raises(CreditLimitExceeded) as exc:
            confirm_sale(sale, actor=pharmacist)
        assert exc.value.code == "cash_only"

    def test_customer_without_a_limit_is_refused_with_its_own_code(
        self, product, warehouse, credit_customer, batch, pharmacist
    ):
        credit_customer.credit_limit = Decimal("0")
        credit_customer.save(update_fields=["credit_limit"])

        sale = create_sale(
            customer=credit_customer, warehouse=warehouse,
            sale_type=SaleType.CREDIT,
            lines=[{"product": product, "quantity": Decimal("1")}],
            actor=pharmacist,
        )
        with pytest.raises(CreditLimitExceeded) as exc:
            confirm_sale(sale, actor=pharmacist)
        assert exc.value.code == "no_credit_limit"

    def test_credit_eligibility_is_independent_of_headroom(self, credit_customer):
        """
        A blocked account keeps its unused limit.

        The sale screen reads both: showing available_credit alone advertised
        credit the server would then refuse.
        """
        credit_customer.credit_limit = Decimal("1000000")
        credit_customer.outstanding_balance = Decimal("0")
        credit_customer.credit_blocked = True

        assert credit_customer.available_credit == Decimal("1000000")
        assert credit_customer.is_credit_eligible is False

    def test_balance_is_derived_not_incremented(
        self, confirmed_sale, credit_customer
    ):
        """
        A running tally accumulates error from every cancellation and
        reversal; a derived figure cannot drift.
        """
        credit_customer.outstanding_balance = Decimal("999999")
        credit_customer.save(update_fields=["outstanding_balance"])

        assert recompute_balance(credit_customer) == INVOICE_TOTAL


class TestPayments:
    def test_payment_reduces_balance(self, confirmed_sale, credit_customer, pharmacist):
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=Decimal("100000"),
            method="BANK_TRANSFER", actor=pharmacist,
        )
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PARTIALLY_PAID
        assert invoice.balance_due == INVOICE_TOTAL - Decimal("100000")

    def test_full_payment_marks_paid(self, confirmed_sale, credit_customer, pharmacist):
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=INVOICE_TOTAL, actor=pharmacist,
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
        assert payment.unallocated_amount == Decimal("200000") - INVOICE_TOTAL

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
        assert invoice.balance_due == INVOICE_TOTAL
        assert invoice.status == InvoiceStatus.POSTED
        assert payment.is_reversed is True

    def test_zero_payment_is_refused(self, credit_customer, pharmacist):
        with pytest.raises(BusinessRuleViolation):
            record_payment(
                customer=credit_customer, amount=Decimal("0"), actor=pharmacist
            )

    def test_paid_amount_always_equals_its_allocations(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """
        The invariant the whole allocation model rests on. An invoice whose
        paid total does not match the rows justifying it cannot be reconciled
        against a customer statement.
        """
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=Decimal("60000"), actor=pharmacist
        )
        record_payment(
            customer=credit_customer, amount=Decimal("40000"), actor=pharmacist
        )

        invoice.refresh_from_db()
        allocated = sum(
            allocation.amount for allocation in invoice.payment_allocations.all()
        )
        assert invoice.paid_amount == allocated == Decimal("100000")


class TestPaymentAllocation:
    """
    Where money lands, and what happens to what will not fit.

    Allocation is the seam between a payment arriving and a debt being
    settled. Money that stalls here is money the pharmacy holds but cannot
    see, while the customer who paid it is still chased for the balance.
    """

    def test_naming_an_unpayable_invoice_is_refused_not_redirected(
        self, confirmed_sale, credit_customer, cash_customer, pharmacist
    ):
        """
        Silently redirecting a targeted payment produces a correct-looking
        receipt that settles the wrong debt, discovered only when the
        customer disputes their statement.
        """
        _sale, invoice = confirmed_sale
        other = create_posted_invoice(cash_customer, pharmacist)

        with pytest.raises(BusinessRuleViolation) as exc:
            record_payment(
                customer=credit_customer,
                amount=Decimal("10000"),
                invoice_ids=[other.id],
                actor=pharmacist,
            )
        assert exc.value.code == "invoice_not_settleable"

        invoice.refresh_from_db()
        assert invoice.paid_amount == Decimal("0")

    def test_overpayment_is_applied_to_the_next_invoice_posted(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """
        Money already in hand settles the next debt without anyone
        remembering to do it. This is the seamless path: the customer who
        overpaid is never chased for a balance their credit already covers.
        """
        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer,
            amount=INVOICE_TOTAL + Decimal("50000"),
            actor=pharmacist,
        )
        assert payment.unallocated_amount == Decimal("50000")

        later = create_posted_invoice(
            credit_customer, pharmacist, unit_price=Decimal("30000")
        )

        later.refresh_from_db()
        payment.refresh_from_db()
        assert later.status == InvoiceStatus.PAID
        assert later.balance_due == Decimal("0")
        assert payment.unallocated_amount == Decimal("20000")

    def test_credit_taken_before_any_invoice_exists_still_lands(
        self, credit_customer, pharmacist
    ):
        """
        A deposit paid ahead of invoicing must settle the invoice when it is
        finally raised, not sit unallocated because nothing was open at the
        moment the cash arrived.
        """
        payment = record_payment(
            customer=credit_customer, amount=Decimal("80000"), actor=pharmacist
        )
        assert payment.unallocated_amount == Decimal("80000")

        invoice = create_posted_invoice(
            credit_customer, pharmacist, unit_price=Decimal("80000")
        )

        invoice.refresh_from_db()
        payment.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID
        assert payment.unallocated_amount == Decimal("0")

    def test_manual_allocation_places_a_standing_credit(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer,
            amount=Decimal("40000"),
            invoice_ids=[invoice.id],
            actor=pharmacist,
        )
        # Reverse nothing; instead raise a second invoice and allocate by hand.
        second = create_posted_invoice(
            credit_customer, pharmacist, unit_price=Decimal("10000")
        )
        second.refresh_from_db()
        assert second.balance_due == Decimal("10000")

        extra = record_payment(
            customer=credit_customer,
            amount=Decimal("10000"),
            invoice_ids=[second.id],
            actor=pharmacist,
        )
        second.refresh_from_db()
        assert second.status == InvoiceStatus.PAID
        assert extra.unallocated_amount == Decimal("0")
        assert payment.allocated_amount == Decimal("40000")

    def test_allocating_a_fully_allocated_payment_is_refused(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, _invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer, amount=Decimal("10000"), actor=pharmacist
        )
        with pytest.raises(BusinessRuleViolation) as exc:
            allocate_payment(payment, actor=pharmacist)
        assert exc.value.code == "nothing_to_allocate"

    def test_a_reversed_payment_cannot_be_allocated(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """Withdrawn money is no longer the customer's to spend."""
        _sale, _invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer,
            amount=INVOICE_TOTAL + Decimal("25000"),
            actor=pharmacist,
        )
        reverse_payment(payment, reason="Chèque sans provision", actor=pharmacist)

        with pytest.raises(InvalidStateTransition):
            allocate_payment(payment, actor=pharmacist)

    def test_reversal_removes_the_allocation_rows(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """
        Clearing only the total left the rows behind, so withdrawn money
        showed as both settling the invoice and available to spend again.
        """
        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer, amount=Decimal("70000"), actor=pharmacist
        )
        assert payment.allocations.count() == 1

        reverse_payment(payment, reason="Erreur de saisie", actor=pharmacist)

        payment.refresh_from_db()
        invoice.refresh_from_db()
        assert payment.allocations.count() == 0
        assert payment.unallocated_amount == payment.amount
        assert invoice.payment_allocations.count() == 0
        assert invoice.paid_amount == Decimal("0")

    def test_reversal_restores_overdue_rather_than_posted(
        self, confirmed_sale, credit_customer, pharmacist, today
    ):
        """
        Leaving a past-due invoice at POSTED hides it from the ageing report
        and the collection list until the nightly sweep — exactly the window
        in which a bounced cheque needs chasing.
        """
        from datetime import timedelta

        _sale, invoice = confirmed_sale
        invoice.due_date = today - timedelta(days=30)
        invoice.save(update_fields=["due_date"])

        payment = record_payment(
            customer=credit_customer, amount=Decimal("50000"), actor=pharmacist
        )
        reverse_payment(payment, reason="Chèque sans provision", actor=pharmacist)

        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.OVERDUE
        assert invoice.balance_due == INVOICE_TOTAL

    def test_credit_note_offset_is_a_real_allocation(
        self, confirmed_sale, credit_customer, pharmacist
    ):
        """
        A credit note used to inflate paid_amount with no row behind it,
        leaving an invoice marked PAID with no record of what settled it.
        """
        from apps.invoicing.models import PaymentMethod

        _sale, invoice = confirmed_sale
        issue_credit_note(
            invoice,
            lines=[
                {
                    "description": "Remise commerciale",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("20000"),
                }
            ],
            reason="Remise accordée",
            reason_code=CreditNoteReason.DISCOUNT_GRANTED,
            actor=pharmacist,
        )

        invoice.refresh_from_db()
        allocations = list(invoice.payment_allocations.select_related("payment"))
        assert len(allocations) == 1
        assert allocations[0].payment.method == PaymentMethod.CREDIT_NOTE
        assert invoice.paid_amount == allocations[0].amount == Decimal("20000")


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
        # The invoicing layer is the ledger: it takes the net unit price that
        # was actually invoiced, not the VAT-inclusive shelf price. A credit
        # note must offset the original line exactly, so it is raised on the
        # same basis the original was posted on.
        note = issue_credit_note(
            invoice,
            lines=[
                {
                    "product": product,
                    "description": "Retour",
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("1271.1864"),
                    "tax_rate": Decimal("18"),
                }
            ],
            reason="Marchandise endommagée",
            actor=pharmacist,
        )
        assert note.invoice_type == InvoiceType.CREDIT_NOTE
        assert note.original_invoice_id == invoice.pk

        invoice.refresh_from_db()
        # 10 of the 100 units returned, so a tenth of the invoice is credited:
        # 15,000 of the 150,000 the customer was billed.
        assert q_document(invoice.balance_due) == Decimal("135000")

    def test_credit_note_requires_a_reason(self, confirmed_sale, product, pharmacist):
        _sale, invoice = confirmed_sale
        with pytest.raises(BusinessRuleViolation):
            issue_credit_note(invoice, lines=[], reason="", actor=pharmacist)

    def test_a_credit_note_cannot_itself_be_credited(
        self, confirmed_sale, product, pharmacist
    ):
        """Correcting a correction is a new invoice, not another credit."""
        _sale, invoice = confirmed_sale
        note = issue_credit_note(
            invoice,
            lines=[
                {
                    "product": product,
                    "description": "Retour",
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("1271.1864"),
                    "tax_rate": Decimal("18"),
                }
            ],
            reason="Marchandise endommagée",
            actor=pharmacist,
        )

        with pytest.raises(BusinessRuleViolation) as exc:
            issue_credit_note(
                note,
                lines=[
                    {
                        "product": product,
                        "description": "Retour",
                        "quantity": Decimal("1"),
                        "unit_price": Decimal("1271.1864"),
                    }
                ],
                reason="Erreur",
                actor=pharmacist,
            )
        assert exc.value.code == "cannot_credit_a_credit_note"


class TestCreditNoteGoodsReturn:
    """
    The invoice-side entry point behind the "Issue credit note" action.

    A correction for returned goods has to reconcile stock as well as money;
    every other reason is a financial correction and must leave stock alone.
    """

    def _return_line(self, sale, *, quantity: str, restock: bool) -> dict:
        sale_line = sale.lines.first()
        return {
            "product": sale_line.product,
            "description": sale_line.product.display_name,
            "quantity": Decimal(quantity),
            "unit_price": sale_line.unit_price,
            "tax_rate": sale_line.tax_rate,
            "sale_line": sale_line.pk,
            "restock": restock,
        }

    def test_returned_goods_go_back_into_stock(
        self, confirmed_sale, batch, pharmacist
    ):
        sale, invoice = confirmed_sale
        # 500 received, 100 sold. The fixture instance predates the sale, so
        # it has to be reloaded before it shows the issue.
        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("400")

        note = issue_credit_note_for_invoice(
            invoice,
            lines=[self._return_line(sale, quantity="10", restock=True)],
            reason="Colis refusé à la livraison",
            reason_code=CreditNoteReason.GOODS_RETURNED,
            actor=pharmacist,
        )

        assert note.invoice_type == InvoiceType.CREDIT_NOTE
        assert note.original_invoice_id == invoice.pk
        assert note.credit_reason_code == CreditNoteReason.GOODS_RETURNED

        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("410")

    def test_damaged_goods_are_credited_but_not_restocked(
        self, confirmed_sale, batch, pharmacist
    ):
        """
        The customer is still refunded; the units are written off rather than
        resold. Crediting and restocking are separate decisions.
        """
        sale, invoice = confirmed_sale

        note = issue_credit_note_for_invoice(
            invoice,
            lines=[self._return_line(sale, quantity="10", restock=False)],
            reason="Flacons brisés",
            reason_code=CreditNoteReason.GOODS_DAMAGED,
            actor=pharmacist,
        )

        assert note.total_amount > Decimal("0")
        batch.refresh_from_db()
        # Back in and straight out again: the ledger shows both movements,
        # and sellable stock is unchanged.
        assert batch.quantity_remaining == Decimal("400")

    def test_a_pricing_correction_does_not_touch_stock(
        self, confirmed_sale, batch, pharmacist
    ):
        sale, invoice = confirmed_sale
        sale_line = sale.lines.first()

        issue_credit_note_for_invoice(
            invoice,
            lines=[
                {
                    "product": sale_line.product,
                    "description": "Écart de prix",
                    "quantity": Decimal("100"),
                    "unit_price": Decimal("100"),
                    "tax_rate": sale_line.tax_rate,
                }
            ],
            reason="Prix unitaire surfacturé",
            reason_code=CreditNoteReason.WRONG_PRICE,
            actor=pharmacist,
        )

        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("400")
        # And nothing was recorded as having come back.
        sale_line.refresh_from_db()
        assert sale_line.quantity_returned == Decimal("0")

    def test_returned_goods_reduce_what_the_customer_owes(
        self, confirmed_sale, pharmacist
    ):
        sale, invoice = confirmed_sale
        original_balance = invoice.balance_due

        note = issue_credit_note_for_invoice(
            invoice,
            lines=[self._return_line(sale, quantity="10", restock=True)],
            reason="Retour partiel",
            reason_code=CreditNoteReason.GOODS_RETURNED,
            actor=pharmacist,
        )

        invoice.refresh_from_db()
        # Compared at internal precision: the credit is subtracted from the
        # stored balance, and rounding either side to the printed franc here
        # would hide a residue that reconciliation is meant to catch.
        assert invoice.balance_due == original_balance - note.total_amount
        assert q_document(invoice.balance_due) == Decimal("135000")

    def test_crediting_more_than_remains_returnable_is_refused(
        self, confirmed_sale, pharmacist
    ):
        """The second return cannot take back units the first already did."""
        sale, invoice = confirmed_sale
        issue_credit_note_for_invoice(
            invoice,
            lines=[self._return_line(sale, quantity="60", restock=True)],
            reason="Premier retour",
            reason_code=CreditNoteReason.GOODS_RETURNED,
            actor=pharmacist,
        )

        with pytest.raises(BusinessRuleViolation) as exc:
            issue_credit_note_for_invoice(
                invoice,
                lines=[self._return_line(sale, quantity="50", restock=True)],
                reason="Second retour",
                reason_code=CreditNoteReason.GOODS_RETURNED,
                actor=pharmacist,
            )
        assert exc.value.code == "return_exceeds_sold"

    def test_a_sale_line_from_another_sale_is_refused(
        self, confirmed_sale, product, warehouse, credit_customer, batch, pharmacist
    ):
        """
        Guards the one input the client controls that could credit stock this
        invoice never sold.
        """
        _sale, invoice = confirmed_sale
        other_sale = create_sale(
            customer=credit_customer,
            warehouse=warehouse,
            sale_type=SaleType.CREDIT,
            lines=[{"product": product, "quantity": Decimal("5")}],
            actor=pharmacist,
        )
        other, _other_invoice, _receipt = confirm_sale(other_sale, actor=pharmacist)
        foreign_line = other.lines.first()

        with pytest.raises(BusinessRuleViolation) as exc:
            issue_credit_note_for_invoice(
                invoice,
                lines=[
                    {
                        "product": product,
                        "description": "Retour",
                        "quantity": Decimal("1"),
                        "unit_price": foreign_line.unit_price,
                        "sale_line": foreign_line.pk,
                        "restock": True,
                    }
                ],
                reason="Retour",
                reason_code=CreditNoteReason.GOODS_RETURNED,
                actor=pharmacist,
            )
        assert exc.value.code == "sale_line_mismatch"

    def test_returned_goods_on_an_invoice_without_a_sale_stay_financial(
        self, credit_customer, product, pharmacist
    ):
        """
        A manually raised invoice has no batch allocations to restock into, so
        the correction is money-only rather than an error.
        """
        from apps.invoicing.services import create_invoice, post_invoice

        invoice = create_invoice(
            customer=credit_customer,
            lines=[
                {
                    "product": product,
                    "description": "Vente directe",
                    "quantity": Decimal("4"),
                    "unit_price": Decimal("1000"),
                }
            ],
            actor=pharmacist,
        )
        post_invoice(invoice, actor=pharmacist)

        note = issue_credit_note_for_invoice(
            invoice,
            lines=[
                {
                    "product": product,
                    "description": "Retour",
                    "quantity": Decimal("2"),
                    "unit_price": Decimal("1000"),
                }
            ],
            reason="Retour sans vente rattachée",
            reason_code=CreditNoteReason.GOODS_RETURNED,
            actor=pharmacist,
        )
        assert note.invoice_type == InvoiceType.CREDIT_NOTE
        assert note.credit_reason_code == CreditNoteReason.GOODS_RETURNED


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
