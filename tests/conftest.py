"""
Shared pytest fixtures.

Fixtures build a minimal but *coherent* dataset — an approved supplier, a
priced product, a customer with credit terms — because most of the rules under
test are relational: a credit sale needs a customer with a NIF and a limit, and
a FIFO pick needs at least two batches with different expiries.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Seed RBAC and numbering once per session, after the schema is built."""
    with django_db_blocker.unblock():
        call_command("seed_rbac", verbosity=0)
        call_command("seed_sequences", verbosity=0)


@pytest.fixture
def today():
    return timezone.localdate()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def _make_user(email: str, role_code: str, **extra):
    from apps.accounts.models import Role, User

    user = User.objects.create_user(
        email=email,
        password="TestPass!2026#Secure",
        first_name=extra.pop("first_name", "Test"),
        last_name=extra.pop("last_name", "User"),
        **extra,
    )
    user.roles.add(Role.objects.get(code=role_code))
    return user


@pytest.fixture
def admin_user(db):
    return _make_user("admin@test.bi", "system-administrator", first_name="Admin")


@pytest.fixture
def pharmacist(db):
    return _make_user("pharmacist@test.bi", "pharmacist", first_name="Pharma")


@pytest.fixture
def technician(db):
    return _make_user("tech@test.bi", "pharmacy-technician", first_name="Tech")


@pytest.fixture
def store_manager(db):
    return _make_user("manager@test.bi", "store-manager", first_name="Manager")


@pytest.fixture
def inventory_officer(db):
    return _make_user("officer@test.bi", "inventory-officer", first_name="Officer")


@pytest.fixture
def auditor(db):
    return _make_user("auditor@test.bi", "auditor", first_name="Auditor")


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


@pytest.fixture
def warehouse(db):
    from apps.inventory.models import Warehouse

    return Warehouse.objects.create(
        code="WH-TEST", name_fr="Entrepôt de test", city="Bujumbura", is_default=True
    )


@pytest.fixture
def category(db):
    from apps.catalog.models import Category

    return Category.objects.create(code="TEST", name_fr="Catégorie de test")


@pytest.fixture
def unit(db):
    from apps.catalog.models import UnitOfMeasure

    return UnitOfMeasure.objects.create(code="BTE", name_fr="Boîte")


@pytest.fixture
def product(db, category, unit, admin_user):
    from apps.catalog.services import create_medicine

    return create_medicine(
        actor=admin_user,
        name="Paracétamol",
        generic_name="Paracetamol",
        strength="500 mg",
        dosage_form="TABLET",
        category=category,
        unit_of_measure=unit,
        unit_cost=Decimal("1000"),
        selling_price=Decimal("1500"),
        vat_rate=Decimal("18"),
        reorder_level=Decimal("100"),
    )


@pytest.fixture
def exempt_product(db, category, unit, admin_user):
    """A VAT-exempt line, to prove tax is not applied where it should not be."""
    from apps.catalog.services import create_medicine

    return create_medicine(
        actor=admin_user,
        name="Sérum physiologique",
        dosage_form="SOLUTION",
        category=category,
        unit_of_measure=unit,
        unit_cost=Decimal("500"),
        selling_price=Decimal("800"),
        vat_rate=Decimal("18"),
        is_vat_exempt=True,
    )


@pytest.fixture
def supplier(db, admin_user):
    from apps.partners.services import approve_supplier, create_supplier

    supplier = create_supplier(
        actor=admin_user, name="Fournisseur Test", country="Kenya"
    )
    approve_supplier(supplier, notes="Approuvé pour les tests", actor=admin_user)
    return supplier


@pytest.fixture
def cash_customer(db, admin_user):
    from apps.partners.services import create_customer

    return create_customer(
        actor=admin_user,
        business_name="Pharmacie Comptant",
        customer_type="PHARMACY",
        payment_terms="CASH",
    )


@pytest.fixture
def credit_customer(db, admin_user):
    from apps.partners.services import create_customer

    return create_customer(
        actor=admin_user,
        business_name="Clinique Crédit",
        customer_type="CLINIC",
        nif="4000123456",
        phone="79123456",
        payment_terms="NET_30",
        credit_limit=Decimal("1000000"),
    )


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------


@pytest.fixture
def batch(db, product, warehouse, supplier, inventory_officer, today):
    """A single long-dated batch of 500 units."""
    from apps.inventory.services import receive_stock

    stock_batch, _movement = receive_stock(
        product=product,
        warehouse=warehouse,
        batch_number="LOT-A",
        expiry_date=today + timedelta(days=365),
        quantity=Decimal("500"),
        unit_cost=Decimal("1000"),
        supplier=supplier,
        performed_by=inventory_officer,
    )
    return stock_batch


@pytest.fixture
def two_batches(db, product, warehouse, supplier, inventory_officer, today):
    """
    Two batches with different expiries.

    The short-dated one is received *second*, so a correct FEFO pick must
    choose it first — proving the ordering is by expiry, not insertion.
    """
    from apps.inventory.services import receive_stock

    long_dated, _ = receive_stock(
        product=product,
        warehouse=warehouse,
        batch_number="LOT-LONG",
        expiry_date=today + timedelta(days=365),
        quantity=Decimal("300"),
        unit_cost=Decimal("1000"),
        supplier=supplier,
        performed_by=inventory_officer,
    )
    short_dated, _ = receive_stock(
        product=product,
        warehouse=warehouse,
        batch_number="LOT-SHORT",
        expiry_date=today + timedelta(days=60),
        quantity=Decimal("200"),
        unit_cost=Decimal("1100"),
        supplier=supplier,
        performed_by=inventory_officer,
    )
    return long_dated, short_dated


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def auth_client(api_client):
    """Factory returning an APIClient authenticated as the given user."""

    def _authenticate(user):
        from rest_framework_simplejwt.tokens import RefreshToken

        token = RefreshToken.for_user(user)
        client = type(api_client)()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
        return client

    return _authenticate
