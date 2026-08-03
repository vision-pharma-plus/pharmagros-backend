"""
Payment allocations as seen from the invoice.

`Payment` links to invoices many-to-many through `PaymentAllocation`, so an
invoice's `paid_amount` is the *output* of an allocation history. These cases
cover exposing that history on the invoice detail response, and the one
distinction the balance alone cannot carry: an invoice cleared by a credit
note reads as settled but was never paid.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.invoicing.models import InvoiceStatus, PaymentMethod
from apps.invoicing.services import issue_credit_note, record_payment
from tests.test_sales_invoicing import INVOICE_TOTAL, confirmed_sale  # noqa: F401


def _detail(client, invoice):
    return client.get(reverse("invoice-detail", args=[invoice.id]))


class TestAllocationsOnTheInvoice:
    def test_absent_before_any_payment(self, auth_client, confirmed_sale, pharmacist):
        _sale, invoice = confirmed_sale

        response = _detail(auth_client(pharmacist), invoice)

        assert response.status_code == 200
        assert response.data["payment_allocations"] == []
        assert response.data["settled_by_credit_note"] is False

    def test_lists_what_settled_the_invoice(
        self, auth_client, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer,
            amount=Decimal("100000"),
            method="BANK_TRANSFER",
            bank_reference="TRF-99",
            actor=pharmacist,
        )

        response = _detail(auth_client(pharmacist), invoice)
        allocations = response.data["payment_allocations"]

        assert len(allocations) == 1
        assert allocations[0]["method"] == "BANK_TRANSFER"
        assert allocations[0]["bank_reference"] == "TRF-99"
        assert Decimal(allocations[0]["amount"]) == Decimal("100000")
        # The person who took the money, so a disputed payment has a name
        # attached to it without opening the payment record.
        assert allocations[0]["received_by_name"]

    def test_several_instalments_are_all_listed(
        self, auth_client, confirmed_sale, credit_customer, pharmacist
    ):
        """
        The case a single 'amount paid' figure hides: three part payments and
        one payment of the same total are indistinguishable without this.
        """
        _sale, invoice = confirmed_sale
        for amount in (Decimal("50000"), Decimal("30000"), Decimal("20000")):
            record_payment(
                customer=credit_customer, amount=amount, actor=pharmacist,
            )

        response = _detail(auth_client(pharmacist), invoice)
        allocations = response.data["payment_allocations"]

        assert len(allocations) == 3
        assert sum(Decimal(row["amount"]) for row in allocations) == Decimal("100000")

    def test_newest_first(
        self, auth_client, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, invoice = confirmed_sale
        first = record_payment(
            customer=credit_customer, amount=Decimal("10000"), actor=pharmacist,
        )
        second = record_payment(
            customer=credit_customer, amount=Decimal("20000"), actor=pharmacist,
        )

        response = _detail(auth_client(pharmacist), invoice)
        references = [row["payment_reference"] for row in response.data["payment_allocations"]]

        assert references == [second.reference, first.reference]

    def test_reversing_a_payment_drops_its_allocation(
        self, auth_client, confirmed_sale, credit_customer, pharmacist
    ):
        """
        `reverse_payment` deletes the allocation rows rather than flagging
        them, so withdrawn money is not simultaneously settling this invoice
        and available to spend elsewhere. The consequence for this response is
        that a reversed payment leaves the invoice's list entirely — the
        evidence of the bounced cheque lives on the payment record, which
        keeps `is_reversed` and its reason.
        """
        from apps.invoicing.services import reverse_payment

        _sale, invoice = confirmed_sale
        payment = record_payment(
            customer=credit_customer,
            amount=Decimal("100000"),
            method="CHEQUE",
            actor=pharmacist,
        )
        reverse_payment(payment, reason="Cheque returned", actor=pharmacist)

        response = _detail(auth_client(pharmacist), invoice)

        assert response.data["payment_allocations"] == []
        # The balance is restored, so the screen is consistent: nothing is
        # listed as settling the invoice and nothing is shown as settled.
        invoice.refresh_from_db()
        assert invoice.balance_due == INVOICE_TOTAL


class TestSettledByCreditNote:
    def test_credit_note_offset_is_flagged_and_listed(
        self, auth_client, confirmed_sale, pharmacist
    ):
        """
        A credit note clears the balance through a Payment carrying
        method=CREDIT_NOTE. That leaves `balance_due` at zero exactly as cash
        would, so without the flag the invoice reads as paid when no money
        ever arrived.
        """
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
            reason="Commercial discount",
            actor=pharmacist,
        )

        response = _detail(auth_client(pharmacist), invoice)
        allocations = response.data["payment_allocations"]

        assert response.data["settled_by_credit_note"] is True
        assert len(allocations) == 1
        assert allocations[0]["method"] == PaymentMethod.CREDIT_NOTE

    def test_cash_settlement_is_not_flagged(
        self, auth_client, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=INVOICE_TOTAL, actor=pharmacist,
        )
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID

        response = _detail(auth_client(pharmacist), invoice)

        assert response.data["settled_by_credit_note"] is False


class TestPaymentProgress:
    def test_reflects_a_part_payment(
        self, auth_client, confirmed_sale, credit_customer, pharmacist
    ):
        _sale, invoice = confirmed_sale
        record_payment(
            customer=credit_customer,
            amount=INVOICE_TOTAL / 4,
            actor=pharmacist,
        )

        response = _detail(auth_client(pharmacist), invoice)

        assert Decimal(response.data["payment_progress"]) == pytest.approx(
            Decimal("25"), abs=Decimal("0.01")
        )


class TestQueryCount:
    def test_allocations_do_not_scale_queries(
        self, auth_client, confirmed_sale, credit_customer, pharmacist,
        django_assert_max_num_queries,
    ):
        """
        The detail serializer reaches through every allocation to its payment
        and payee. Without the prefetch that is two queries per row, so the
        cost of opening an invoice would grow with how often it was paid.
        """
        _sale, invoice = confirmed_sale
        for _ in range(5):
            record_payment(
                customer=credit_customer, amount=Decimal("10000"), actor=pharmacist,
            )

        client = auth_client(pharmacist)
        # Warm any per-session lookups (auth, permissions) so the assertion
        # measures the serializer rather than the login path.
        _detail(client, invoice)

        with django_assert_max_num_queries(15):
            response = _detail(client, invoice)

        assert len(response.data["payment_allocations"]) == 5

    def test_list_does_not_prefetch_allocations(
        self, auth_client, confirmed_sale, credit_customer, pharmacist
    ):
        """
        The list renders a progress bar from figures it already has. Loading
        allocations for 25 rows to display none of them would be pure cost.
        """
        _sale, _invoice = confirmed_sale
        record_payment(
            customer=credit_customer, amount=Decimal("10000"), actor=pharmacist,
        )

        response = auth_client(pharmacist).get(reverse("invoice-list"))

        assert response.status_code == 200
        assert "payment_allocations" not in response.data["results"][0]
        # Both figures the list's bar is derived from must still be present.
        assert "paid_amount" in response.data["results"][0]
        assert "total_amount" in response.data["results"][0]
