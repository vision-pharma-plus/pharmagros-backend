"""
Receiving a whole delivery in one action.

This is the endpoint behind the receiving screen. The properties that matter
are that a lot number and expiry are never optional, that the delivery is
all-or-nothing, and that opening stock is journalled as an opening balance
rather than disguised as a purchase.
"""
import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.inventory.models import MovementType, StockBatch, StockMovement

URL = "/api/v1/inventory/receive/bulk/"


@pytest.fixture
def future():
    return timezone.localdate() + datetime.timedelta(days=365)


def _line(product, warehouse, **over):
    data = {
        "product": str(product.id),
        "warehouse": str(warehouse.id),
        "batch_number": "LOT-A",
        "expiry_date": str(timezone.localdate() + datetime.timedelta(days=365)),
        "quantity": "100",
        "unit_cost": "50",
    }
    data.update(over)
    return data


@pytest.mark.django_db
def test_receives_several_products_in_one_call(
    auth_client, inventory_officer, product, exempt_product, warehouse
):
    client = auth_client(inventory_officer)
    resp = client.post(URL, {
        "source_reference": "DN-4471",
        "lines": [
            _line(product, warehouse, batch_number="LOT-A", quantity="100"),
            _line(exempt_product, warehouse, batch_number="LOT-B", quantity="40"),
        ],
    }, format="json")
    assert resp.status_code == 201, resp.data

    a = StockBatch.objects.get(batch_number="LOT-A")
    b = StockBatch.objects.get(batch_number="LOT-B")
    assert a.quantity_remaining == Decimal("100")
    assert b.quantity_remaining == Decimal("40")
    # The delivery note reaches the ledger, which is what ties a movement back
    # to the paperwork during an audit.
    assert a.movements.get().source_reference == "DN-4471"


@pytest.mark.django_db
def test_batch_number_and_expiry_are_required(
    auth_client, inventory_officer, product, warehouse
):
    """The whole point of the screen: stock cannot enter without a lot."""
    client = auth_client(inventory_officer)

    no_lot = _line(product, warehouse)
    del no_lot["batch_number"]
    resp = client.post(URL, {"lines": [no_lot]}, format="json")
    assert resp.status_code == 400
    assert "batch_number" in str(resp.data)

    no_expiry = _line(product, warehouse)
    del no_expiry["expiry_date"]
    resp = client.post(URL, {"lines": [no_expiry]}, format="json")
    assert resp.status_code == 400
    assert "expiry_date" in str(resp.data)


@pytest.mark.django_db
def test_expired_stock_is_refused(auth_client, inventory_officer, product, warehouse):
    client = auth_client(inventory_officer)
    past = timezone.localdate() - datetime.timedelta(days=1)
    resp = client.post(URL, {
        "lines": [_line(product, warehouse, expiry_date=str(past))],
    }, format="json")
    assert resp.status_code == 400
    assert "expiry_date" in str(resp.data)


@pytest.mark.django_db
def test_whole_delivery_rolls_back_on_a_bad_line(
    auth_client, inventory_officer, product, exempt_product, warehouse, future
):
    """
    A failure on the second line must not leave the first one booked.

    The clerk would otherwise be holding a half-received delivery with no
    indication of which lines made it in.
    """
    client = auth_client(inventory_officer)

    # An existing lot whose expiry disagrees with what the second line claims,
    # which receive_stock refuses.
    StockBatch.objects.create(
        product=exempt_product, warehouse=warehouse, batch_number="CONFLICT",
        expiry_date=future, quantity_received=Decimal("0"),
        quantity_remaining=Decimal("0"), quantity_reserved=Decimal("0"),
        unit_cost=Decimal("10"), landed_unit_cost=Decimal("10"),
    )
    other_expiry = future + datetime.timedelta(days=30)

    resp = client.post(URL, {
        "lines": [
            _line(product, warehouse, batch_number="GOOD-1"),
            _line(
                exempt_product, warehouse,
                batch_number="CONFLICT", expiry_date=str(other_expiry),
            ),
        ],
    }, format="json")

    assert resp.status_code == 400, resp.data
    # The good line is gone too — that is the point.
    assert not StockBatch.objects.filter(batch_number="GOOD-1").exists()


@pytest.mark.django_db
def test_duplicate_lot_within_one_delivery_is_rejected(
    auth_client, inventory_officer, product, warehouse
):
    """
    Two lines for the same product/lot/warehouse would weighted-average the
    second cost against the first line's freshly written balance, silently
    moving a cost the clerk had already entered.
    """
    client = auth_client(inventory_officer)
    resp = client.post(URL, {
        "lines": [
            _line(product, warehouse, batch_number="SAME", unit_cost="50"),
            _line(product, warehouse, batch_number="SAME", unit_cost="90"),
        ],
    }, format="json")
    assert resp.status_code == 400
    assert not StockBatch.objects.filter(batch_number="SAME").exists()


@pytest.mark.django_db
def test_opening_balance_is_journalled_as_such(
    auth_client, inventory_officer, product, warehouse
):
    """
    Go-live stock is asserted from a physical count, not bought from a
    supplier. Journalling it as a RECEIPT would overstate purchase history by
    the entire starting inventory.
    """
    client = auth_client(inventory_officer)
    resp = client.post(URL, {
        "is_opening_balance": True,
        "lines": [_line(product, warehouse, batch_number="OPEN-1")],
    }, format="json")
    assert resp.status_code == 201, resp.data

    movement = StockMovement.objects.get(batch__batch_number="OPEN-1")
    assert movement.movement_type == MovementType.OPENING
    assert movement.source_type == "inventory.OpeningBalance"
    assert movement.quantity_delta == Decimal("100")


@pytest.mark.django_db
def test_ordinary_receipt_is_not_an_opening_balance(
    auth_client, inventory_officer, product, warehouse
):
    client = auth_client(inventory_officer)
    resp = client.post(URL, {
        "lines": [_line(product, warehouse, batch_number="NORM-1")],
    }, format="json")
    assert resp.status_code == 201, resp.data
    movement = StockMovement.objects.get(batch__batch_number="NORM-1")
    assert movement.movement_type == MovementType.RECEIPT


@pytest.mark.django_db
def test_lines_may_target_different_warehouses(
    auth_client, inventory_officer, product, warehouse, db
):
    """Per-line override: a cold-chain item goes somewhere else on the same run."""
    from apps.inventory.models import Warehouse

    cold = Warehouse.objects.create(
        code="COLD", name_fr="Chambre froide", is_cold_chain=True,
    )
    client = auth_client(inventory_officer)
    resp = client.post(URL, {
        "lines": [
            _line(product, warehouse, batch_number="AMB-1"),
            _line(product, cold, batch_number="COLD-1"),
        ],
    }, format="json")
    assert resp.status_code == 201, resp.data

    assert StockBatch.objects.get(batch_number="AMB-1").warehouse_id == warehouse.id
    assert StockBatch.objects.get(batch_number="COLD-1").warehouse_id == cold.id


@pytest.mark.django_db
def test_requires_the_receive_permission(auth_client, auditor, product, warehouse):
    """An auditor can read stock but must not be able to book any."""
    client = auth_client(auditor)
    resp = client.post(URL, {
        "lines": [_line(product, warehouse, batch_number="NOPE")],
    }, format="json")
    assert resp.status_code in (403, 401)
    assert not StockBatch.objects.filter(batch_number="NOPE").exists()


@pytest.mark.django_db
def test_empty_delivery_is_rejected(auth_client, inventory_officer):
    client = auth_client(inventory_officer)
    resp = client.post(URL, {"lines": []}, format="json")
    assert resp.status_code == 400
