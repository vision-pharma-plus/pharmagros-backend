"""
Who may grant a discount, and how large it may be.

Two separate controls, tested separately because they fail for different
reasons and carry different messages:

  * `sales.apply_discount` decides *whether* an operator may discount at all.
    It sits with management (store manager, administrator) and not with the
    counter (technician, pharmacist).
  * MAX_MANUAL_DISCOUNT_PERCENT decides *how much*. It is absolute — no
    permission lifts it, including the administrator's.

A customer's standing contractual discount is exempt from both: it was agreed
when the account was opened, not chosen at the counter.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.conf import settings

from apps.core.exceptions import BusinessRuleViolation
from apps.partners.services import create_customer
from apps.sales.models import SaleType
from apps.sales.services import create_sale, update_sale

pytestmark = pytest.mark.django_db

CEILING = Decimal(settings.MAX_MANUAL_DISCOUNT_PERCENT)


@pytest.fixture
def sale_for(product, warehouse, cash_customer, batch):
    """
    Build a one-line draft sale as a given user, discounted at the header
    (`discount`) or on the line (`line_discount`).

    Both paths are exercised throughout because they run through different
    call sites into the same check, and a gate applied to only one of them
    would leave the other wide open.
    """

    def build(actor, *, discount=None, line_discount=None):
        line = {"product": product, "quantity": Decimal("5")}
        if line_discount is not None:
            line["discount_percent"] = line_discount

        kwargs = {}
        if discount is not None:
            kwargs["discount_percent"] = discount

        return create_sale(
            customer=cash_customer,
            warehouse=warehouse,
            sale_type=SaleType.CASH,
            lines=[line],
            actor=actor,
            **kwargs,
        )

    return build


class TestOnlyAuthorisedRolesMayDiscount:
    def test_administrator_may_discount(self, sale_for, admin_user):
        sale = sale_for(admin_user, discount=Decimal("5"))
        assert sale.discount_percent == Decimal("5")

    def test_store_manager_may_discount(self, sale_for, store_manager):
        sale = sale_for(store_manager, discount=Decimal("5"))
        assert sale.discount_percent == Decimal("5")

    def test_pharmacist_may_not_discount(self, sale_for, pharmacist):
        """
        The counter cannot discount. A pharmacist runs day-to-day sales but
        discounting is management authority, so this is refused even at a
        modest 5%.
        """
        with pytest.raises(BusinessRuleViolation) as caught:
            sale_for(pharmacist, discount=Decimal("5"))
        assert caught.value.code == "discount_not_permitted"

    def test_technician_may_not_discount(self, sale_for, technician):
        with pytest.raises(BusinessRuleViolation) as caught:
            sale_for(technician, discount=Decimal("5"))
        assert caught.value.code == "discount_not_permitted"

    def test_line_discount_is_gated_too(self, sale_for, pharmacist):
        """The line-level field is the same authority as the header one."""
        with pytest.raises(BusinessRuleViolation) as caught:
            sale_for(pharmacist, line_discount=Decimal("5"))
        assert caught.value.code == "discount_not_permitted"

    def test_unattributed_caller_may_not_discount(self, sale_for):
        """
        An import or job with no actor fails closed. An unattributed caller is
        precisely the one that must not be able to reduce revenue.
        """
        with pytest.raises(BusinessRuleViolation) as caught:
            sale_for(None, discount=Decimal("5"))
        assert caught.value.code == "discount_not_permitted"

    def test_a_zero_discount_needs_no_permission(self, sale_for, pharmacist):
        """
        Selling at list price is not discounting. A pharmacist must still be
        able to take an ordinary sale.
        """
        sale = sale_for(pharmacist, discount=Decimal("0"))
        assert sale.discount_percent == Decimal("0")


class TestTheCeilingIsAbsolute:
    def test_at_the_ceiling_is_allowed(self, sale_for, store_manager):
        sale = sale_for(store_manager, discount=CEILING)
        assert sale.discount_percent == CEILING

    def test_above_the_ceiling_is_refused(self, sale_for, store_manager):
        with pytest.raises(BusinessRuleViolation) as caught:
            sale_for(store_manager, discount=CEILING + Decimal("0.01"))
        assert caught.value.code == "discount_limit_exceeded"

    def test_the_administrator_cannot_exceed_it_either(self, sale_for, admin_user):
        """
        The point of the change: a ceiling that a sufficiently senior user can
        lift is not a ceiling. There is no override permission to hold.
        """
        with pytest.raises(BusinessRuleViolation) as caught:
            sale_for(admin_user, discount=Decimal("15"))
        assert caught.value.code == "discount_limit_exceeded"

    def test_no_override_permission_exists(self):
        from apps.accounts.rbac import ALL_CODES

        assert "sales.override_discount_limit" not in ALL_CODES

    def test_line_discount_is_capped_too(self, sale_for, store_manager):
        with pytest.raises(BusinessRuleViolation) as caught:
            sale_for(store_manager, line_discount=Decimal("15"))
        assert caught.value.code == "discount_limit_exceeded"

    def test_the_error_names_the_ceiling(self, sale_for, store_manager):
        """
        The operator has to learn the limit from the refusal, so the number
        appears in both the message and the machine-readable details.
        """
        with pytest.raises(BusinessRuleViolation) as caught:
            sale_for(store_manager, discount=Decimal("15"))
        # The message trims the internal 10.0000 down to a readable '10'; the
        # details carry it at full precision. Compared numerically so the two
        # representations of the same ceiling do not make this brittle.
        assert "10" in str(caught.value)
        assert Decimal(caught.value.details["max_percent"]) == CEILING

    def test_amending_a_draft_is_capped(self, sale_for, store_manager):
        """
        The ceiling must hold on the edit path too, otherwise a compliant draft
        could be amended into a non-compliant one.
        """
        sale = sale_for(store_manager, discount=Decimal("5"))
        with pytest.raises(BusinessRuleViolation) as caught:
            update_sale(sale, discount_percent=Decimal("15"), actor=store_manager)
        assert caught.value.code == "discount_limit_exceeded"

    def test_amending_a_draft_is_gated(self, sale_for, store_manager, pharmacist):
        """A user without authority cannot add a discount by editing."""
        sale = sale_for(store_manager, discount=Decimal("0"))
        with pytest.raises(BusinessRuleViolation) as caught:
            update_sale(sale, discount_percent=Decimal("5"), actor=pharmacist)
        assert caught.value.code == "discount_not_permitted"


class TestStandingCustomerDiscountIsExempt:
    """
    A negotiated rate on the customer record is a contractual term governed by
    `partners.change_customer`, not a counter decision. Capping it would block
    ordinary sales to a customer whose agreed rate is above the ceiling, and
    requiring `sales.apply_discount` would stop a pharmacist serving them.
    """

    @pytest.fixture
    def customer_on_15_percent(self, admin_user):
        return create_customer(
            actor=admin_user,
            business_name="Pharmacie Partenaire",
            customer_type="PHARMACY",
            payment_terms="CASH",
            discount_percent=Decimal("15"),
        )

    def test_it_applies_above_the_ceiling(
        self, product, warehouse, batch, pharmacist, customer_on_15_percent
    ):
        sale = create_sale(
            customer=customer_on_15_percent,
            warehouse=warehouse,
            sale_type=SaleType.CASH,
            lines=[{"product": product, "quantity": Decimal("5")}],
            actor=pharmacist,
        )
        assert sale.lines.get().discount_percent == Decimal("15")

    def test_typing_the_same_rate_is_still_refused(
        self, product, warehouse, batch, pharmacist, customer_on_15_percent
    ):
        """
        The exemption is for the rate the *record* carries, not for the
        customer. An operator who types 15 is making a counter decision and is
        held to the same rules as on any other sale.
        """
        with pytest.raises(BusinessRuleViolation) as caught:
            create_sale(
                customer=customer_on_15_percent,
                warehouse=warehouse,
                sale_type=SaleType.CASH,
                lines=[
                    {
                        "product": product,
                        "quantity": Decimal("5"),
                        "discount_percent": Decimal("15"),
                    }
                ],
                actor=pharmacist,
            )
        assert caught.value.code == "discount_not_permitted"
