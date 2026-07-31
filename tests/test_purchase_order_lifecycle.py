"""
The procurement chain, end to end, through the API.

Create → edit draft → submit → approve → send → receive → stock exists.

The properties pinned here are the ones that make a purchase order a control
rather than a note: only approved suppliers can be ordered from, the requester
cannot approve their own order, an approved order can no longer be edited, and
goods cannot be booked without the batch and expiry printed on the carton.
"""
import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.inventory.models import StockBatch
from apps.purchasing.models import PurchaseOrder, PurchaseOrderStatus

ORDERS = "/api/v1/purchasing/orders/"


@pytest.fixture
def future():
    return timezone.localdate() + datetime.timedelta(days=400)


def _payload(supplier, warehouse, product, **over):
    data = {
        "supplier": str(supplier.id),
        "warehouse": str(warehouse.id),
        "lines": [
            {
                "product": str(product.id),
                "quantity_ordered": "100",
                "unit_cost": "50",
            }
        ],
    }
    data.update(over)
    return data


@pytest.mark.django_db
def test_full_procurement_chain(
    auth_client, admin_user, store_manager, inventory_officer,
    supplier, warehouse, product, future,
):
    """A purchase order becomes stock, one step at a time."""
    buyer = auth_client(store_manager)

    # 1. Raise the order.
    resp = buyer.post(ORDERS, _payload(supplier, warehouse, product), format="json")
    assert resp.status_code == 201, resp.data
    order_id = resp.data["id"]
    assert resp.data["status"] == PurchaseOrderStatus.DRAFT
    assert resp.data["order_number"]           # numbering service allocated one
    assert resp.data["is_editable"] is True

    # 2. Amend the draft — quantity was wrong.
    resp = buyer.put(f"{ORDERS}{order_id}/", _payload(
        supplier, warehouse, product,
        lines=[{
            "product": str(product.id),
            "quantity_ordered": "250",
            "unit_cost": "48",
        }],
    ), format="json")
    assert resp.status_code == 200, resp.data
    assert Decimal(resp.data["lines"][0]["quantity_ordered"]) == Decimal("250")
    assert len(resp.data["lines"]) == 1        # replaced, not appended

    # 3. Submit for approval.
    resp = buyer.post(f"{ORDERS}{order_id}/submit/", {}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["status"] == PurchaseOrderStatus.PENDING_APPROVAL

    # 4. Approve — as somebody else. See the separation-of-duties test below.
    approver = auth_client(admin_user)
    resp = approver.post(f"{ORDERS}{order_id}/approve/", {}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["status"] == PurchaseOrderStatus.APPROVED
    assert resp.data["can_receive"] is True

    # 5. Send to the supplier.
    resp = approver.post(f"{ORDERS}{order_id}/mark-sent/", {}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["status"] == PurchaseOrderStatus.SENT

    # 6. Receive the goods, with the lot read off the carton.
    receiver = auth_client(inventory_officer)
    resp = receiver.post(f"{ORDERS}{order_id}/receive/", {
        "delivery_note_number": "DN-9001",
        "lines": [{
            "purchase_order_line": resp.data["lines"][0]["id"],
            "batch_number": "PO-LOT-1",
            "expiry_date": str(future),
            "quantity_received": "250",
        }],
    }, format="json")
    assert resp.status_code == 201, resp.data

    # 7. The stock exists, tied to the order it came from.
    batch = StockBatch.objects.get(batch_number="PO-LOT-1")
    assert batch.quantity_remaining == Decimal("250")
    assert batch.expiry_date == future
    assert batch.supplier_id == supplier.id
    assert str(batch.purchase_order_id) == str(order_id)

    order = PurchaseOrder.objects.get(pk=order_id)
    assert order.status == PurchaseOrderStatus.RECEIVED


# ---------------------------------------------------------------------------
# The controls
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cannot_order_from_an_unapproved_supplier(
    auth_client, store_manager, admin_user, warehouse, product
):
    """Sourcing from an unvetted supplier is a regulatory finding, not a risk."""
    from apps.partners.services import create_supplier

    unvetted = create_supplier(actor=admin_user, name="Inconnu SARL", country="X")
    client = auth_client(store_manager)

    resp = client.post(
        ORDERS, _payload(unvetted, warehouse, product), format="json",
    )
    # 422: a domain rule refused a well-formed request. See core.exceptions.
    assert resp.status_code == 422, resp.data
    assert "supplier_not_approved" in str(resp.data)


@pytest.mark.django_db
def test_requester_cannot_approve_their_own_order(
    auth_client, store_manager, supplier, warehouse, product
):
    """
    Separation of duties. Without it, one person can order goods to an
    address they control and sign off the purchase themselves.
    """
    client = auth_client(store_manager)
    order_id = client.post(
        ORDERS, _payload(supplier, warehouse, product), format="json",
    ).data["id"]
    client.post(f"{ORDERS}{order_id}/submit/", {}, format="json")

    resp = client.post(f"{ORDERS}{order_id}/approve/", {}, format="json")
    assert resp.status_code == 422, resp.data
    assert "separation_of_duties" in str(resp.data)

    assert PurchaseOrder.objects.get(pk=order_id).status == (
        PurchaseOrderStatus.PENDING_APPROVAL
    )


@pytest.mark.django_db
def test_approved_order_cannot_be_edited(
    auth_client, store_manager, admin_user, supplier, warehouse, product
):
    """
    An approved order is a signature. Letting the requester change the
    quantities afterwards would make the approval meaningless.
    """
    buyer = auth_client(store_manager)
    order_id = buyer.post(
        ORDERS, _payload(supplier, warehouse, product), format="json",
    ).data["id"]
    buyer.post(f"{ORDERS}{order_id}/submit/", {}, format="json")
    auth_client(admin_user).post(f"{ORDERS}{order_id}/approve/", {}, format="json")

    resp = buyer.put(f"{ORDERS}{order_id}/", _payload(
        supplier, warehouse, product,
        lines=[{
            "product": str(product.id),
            "quantity_ordered": "9999",
            "unit_cost": "1",
        }],
    ), format="json")
    assert resp.status_code == 422, resp.data
    assert "document_locked" in str(resp.data)

    order = PurchaseOrder.objects.get(pk=order_id)
    assert order.lines.get().quantity_ordered == Decimal("100")  # untouched


@pytest.mark.django_db
def test_rejected_order_can_be_corrected_and_resubmitted(
    auth_client, store_manager, admin_user, supplier, warehouse, product
):
    """Rejection exists so an order can be fixed, not abandoned."""
    buyer = auth_client(store_manager)
    order_id = buyer.post(
        ORDERS, _payload(supplier, warehouse, product), format="json",
    ).data["id"]
    buyer.post(f"{ORDERS}{order_id}/submit/", {}, format="json")
    auth_client(admin_user).post(
        f"{ORDERS}{order_id}/reject/", {"reason": "Prix trop élevé"}, format="json",
    )

    resp = buyer.put(f"{ORDERS}{order_id}/", _payload(
        supplier, warehouse, product,
        lines=[{
            "product": str(product.id),
            "quantity_ordered": "100",
            "unit_cost": "30",
        }],
    ), format="json")
    assert resp.status_code == 200, resp.data

    resp = buyer.post(f"{ORDERS}{order_id}/submit/", {}, format="json")
    assert resp.status_code == 200
    assert resp.data["status"] == PurchaseOrderStatus.PENDING_APPROVAL


@pytest.mark.django_db
def test_order_cannot_be_left_without_lines(
    auth_client, store_manager, supplier, warehouse, product
):
    client = auth_client(store_manager)
    # Refused either by the serializer (400, allow_empty=False) or by the
    # service rule (422). Both are correct; the point is that it is refused.
    resp = client.post(
        ORDERS, _payload(supplier, warehouse, product, lines=[]), format="json",
    )
    assert resp.status_code in (400, 422), resp.data

    order_id = client.post(
        ORDERS, _payload(supplier, warehouse, product), format="json",
    ).data["id"]
    resp = client.put(
        f"{ORDERS}{order_id}/",
        _payload(supplier, warehouse, product, lines=[]),
        format="json",
    )
    assert resp.status_code in (400, 422), resp.data


@pytest.mark.django_db
def test_editing_recalculates_the_order_total(
    auth_client, store_manager, supplier, warehouse, product
):
    """A changed line must move the header totals, or the document lies."""
    client = auth_client(store_manager)
    created = client.post(
        ORDERS, _payload(supplier, warehouse, product), format="json",
    ).data
    order_id = created["id"]
    original_total = Decimal(created["total_amount"])

    resp = client.put(f"{ORDERS}{order_id}/", _payload(
        supplier, warehouse, product,
        lines=[{
            "product": str(product.id),
            "quantity_ordered": "200",   # doubled
            "unit_cost": "50",
        }],
        freight_cost="1000",
    ), format="json")
    assert resp.status_code == 200, resp.data

    assert Decimal(resp.data["total_amount"]) > original_total
    assert Decimal(resp.data["freight_cost"]) == Decimal("1000")


@pytest.mark.django_db
def test_receiving_requires_batch_and_expiry(
    auth_client, store_manager, admin_user, inventory_officer,
    supplier, warehouse, product,
):
    """The carton has a lot number on it; the receipt must carry it too."""
    buyer = auth_client(store_manager)
    order = buyer.post(
        ORDERS, _payload(supplier, warehouse, product), format="json",
    ).data
    order_id = order["id"]
    line_id = order["lines"][0]["id"]

    buyer.post(f"{ORDERS}{order_id}/submit/", {}, format="json")
    auth_client(admin_user).post(f"{ORDERS}{order_id}/approve/", {}, format="json")

    receiver = auth_client(inventory_officer)
    resp = receiver.post(f"{ORDERS}{order_id}/receive/", {
        "lines": [{
            "purchase_order_line": line_id,
            "quantity_received": "100",
        }],
    }, format="json")
    assert resp.status_code in (400, 422), resp.data
    body = str(resp.data)
    assert "batch_number" in body or "expiry_date" in body
