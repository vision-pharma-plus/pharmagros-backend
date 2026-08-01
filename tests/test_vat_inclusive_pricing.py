"""
VAT-inclusive catalogue pricing.

Catalogue prices are the shelf price — what the customer actually hands over —
and the net-of-VAT base every document line is computed from is derived from
it. These tests pin the round trip in both directions, because an error either
way misprices the entire catalogue by the VAT rate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.money import compute_line, price_ex_tax, q_document
from apps.sales.services import create_sale

pytestmark = pytest.mark.django_db


class TestPriceExTax:
    """Unit-level behaviour of the extraction helper."""

    @pytest.mark.parametrize(
        "ttc,rate,expected",
        [
            ("180", "18", "152.5424"),
            ("1500", "18", "1271.1864"),
            ("1000", "18", "847.4576"),
            ("5000", "10", "4545.4545"),
            # A zero rate must return the price untouched rather than divide
            # by one — exempt lines are exact, not merely close.
            ("180", "0", "180.0000"),
            ("800", "0", "800.0000"),
            ("0", "18", "0.0000"),
        ],
    )
    def test_extracts_the_net_base(self, ttc, rate, expected):
        assert price_ex_tax(ttc, rate) == Decimal(expected)

    @pytest.mark.parametrize("ttc", ["180", "1500", "999", "12345", "1"])
    @pytest.mark.parametrize("rate", ["18", "10", "0"])
    def test_round_trips_back_to_the_shelf_price(self, ttc, rate):
        """
        The customer-facing number is what must survive the round trip.

        Extraction happens at 4 dp and the result is rounded exactly once, at
        the document boundary — so a single unit always bills at the shelf
        price, whatever the rate.
        """
        net = price_ex_tax(ttc, rate)
        line = compute_line("1", net, "0", rate)
        assert q_document(line["total"]) == Decimal(ttc)

    @pytest.mark.parametrize("qty", ["1", "3", "7", "12", "33", "100", "250"])
    def test_multi_unit_lines_stay_exact(self, qty):
        """
        The case worth guarding: 1500 TTC has no exact net representation, so
        a multi-unit line accumulates a sub-franc residue internally. It must
        never reach the printed total.
        """
        net = price_ex_tax("1500", "18")
        line = compute_line(qty, net, "0", "18")
        assert q_document(line["total"]) == Decimal(qty) * 1500

    def test_float_input_is_still_refused(self):
        """The helper must not become a way to smuggle a float into money."""
        with pytest.raises(TypeError):
            price_ex_tax(180.0, "18")


class TestCataloguePricing:
    def test_price_for_returns_the_net_base(self, product):
        """`price_for` is the single point of extraction for document lines."""
        assert product.selling_price == Decimal("1500.0000")
        assert product.price_for() == Decimal("1271.1864")

    def test_price_incl_tax_for_returns_the_shelf_price(self, product):
        assert product.price_incl_tax_for() == Decimal("1500.0000")

    def test_exempt_product_is_not_discounted(self, exempt_product):
        """
        An exempt product carries no VAT to extract. Dividing by 1.18 anyway
        would quietly cut its price by 15%.
        """
        assert exempt_product.price_for() == Decimal("800.0000")
        assert exempt_product.price_incl_tax_for() == Decimal("800.0000")

    def test_margin_is_computed_net_of_vat(self, product):
        """
        `unit_cost` is a purchase cost and carries no VAT. Comparing it against
        the VAT-inclusive selling price would overstate every margin by the
        tax rate — here 50% instead of the true 27%.
        """
        # cost 1000, net price 1271.1864 -> 27.12%
        assert product.margin_percent.quantize(Decimal("0.01")) == Decimal("27.12")


class TestWholesalePricing:
    def test_institutional_price_is_also_vat_inclusive(
        self, product, credit_customer
    ):
        """
        Both catalogue tiers are stored on the same basis, so both are
        extracted the same way. A split basis between them would make
        `price_for` return two different kinds of number.
        """
        product.wholesale_price = Decimal("1180")
        product.save(update_fields=["wholesale_price"])
        credit_customer.customer_type = "CLINIC"
        credit_customer.save(update_fields=["customer_type"])

        if getattr(credit_customer, "is_institutional", False):
            assert product.price_for(credit_customer) == Decimal("1000.0000")
            assert product.price_incl_tax_for(credit_customer) == Decimal("1180.0000")


class TestSaleLinePricing:
    def test_shelf_price_is_what_the_customer_pays(
        self, product, warehouse, cash_customer, pharmacist
    ):
        sale = create_sale(
            customer=cash_customer,
            warehouse=warehouse,
            lines=[{"product": product, "quantity": Decimal("4")}],
            actor=pharmacist,
        )
        assert q_document(sale.total_amount) == Decimal("6000")  # 4 x 1500

    def test_counter_override_is_read_as_vat_inclusive(
        self, product, warehouse, cash_customer, pharmacist
    ):
        """
        An operator typing 180 means 180 paid. Treating the override as a net
        figure would bill 212 and undercharge the VAT on every discounted or
        manually priced line.
        """
        sale = create_sale(
            customer=cash_customer,
            warehouse=warehouse,
            lines=[{
                "product": product,
                "quantity": Decimal("1"),
                "unit_price": Decimal("180"),
            }],
            actor=pharmacist,
        )
        line = sale.lines.first()
        assert line.unit_price == Decimal("152.5424")
        assert q_document(sale.total_amount) == Decimal("180")

    def test_tax_is_the_difference_between_gross_and_net(
        self, product, warehouse, cash_customer, pharmacist
    ):
        """VAT is carved out of the shelf price, never added on top of it."""
        sale = create_sale(
            customer=cash_customer,
            warehouse=warehouse,
            lines=[{"product": product, "quantity": Decimal("1")}],
            actor=pharmacist,
        )
        assert sale.subtotal + sale.tax_amount == sale.total_amount
        assert q_document(sale.total_amount) == Decimal("1500")
