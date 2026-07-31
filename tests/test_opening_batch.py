"""
Where stock enters the system, and where it deliberately cannot.

Stock is booked on the receiving screen, against the carton the lot number is
printed on. Cataloguing a product is a separate act that creates no stock: a
product is routinely catalogued before its first delivery arrives, when nobody
has a carton to read.

The service still accepts opening-batch arguments for API clients and the
importer, so the rules governing that path are pinned here too — most
importantly that a lot number is never invented and never silently merged with
a different expiry.
"""
import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.serializers import MedicineDetailSerializer
from apps.catalog.services import create_medicine, update_medicine
from apps.core.exceptions import BusinessRuleViolation
from apps.inventory.models import MovementType, StockBatch


def _base(category, unit, **over):
    """Minimal valid product payload for the service-layer tests."""
    data = dict(
        name_fr="Test Med",
        category=category,
        unit_of_measure=unit,
        unit_cost=Decimal("100"),
        selling_price=Decimal("150"),
    )
    data.update(over)
    return data


# ---------------------------------------------------------------------------
# Cataloguing a product creates no stock
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_create_without_batch_makes_no_stock(
    auth_client, admin_user, warehouse, category, unit
):
    """The ordinary case: the product form books no stock and raises no error."""
    client = auth_client(admin_user)
    resp = client.post("/api/v1/catalog/medicines/", {
        "name": "Catalogue Only",
        "category": str(category.id), "unit_of_measure": str(unit.id),
        "dosage_form": "TABLET", "unit_cost": "1", "selling_price": "2",
    }, format="json")
    assert resp.status_code == 201, resp.data
    assert StockBatch.objects.filter(product_id=resp.data["id"]).count() == 0


@pytest.mark.django_db
def test_no_batch_fields_creates_no_batch(category, unit):
    m = create_medicine(**_base(category, unit))
    assert StockBatch.objects.filter(product=m).count() == 0


# ---------------------------------------------------------------------------
# The service-level opening-batch path, still used by API clients
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_create_with_manual_batch(
    auth_client, admin_user, warehouse, supplier, category, unit
):
    client = auth_client(admin_user)
    future = timezone.localdate() + datetime.timedelta(days=365)

    resp = client.post("/api/v1/catalog/medicines/", {
        "name": "Furosemide 40mg",
        "category": str(category.id),
        "unit_of_measure": str(unit.id),
        "dosage_form": "TABLET",
        "unit_cost": "112.00",
        "selling_price": "150.00",
        "batch_number": "FUR-2410-A",
        "expiry_date": str(future),
        "opening_quantity": "250",
        "opening_supplier": str(supplier.id),
        "opening_warehouse": str(warehouse.id),
    }, format="json")
    assert resp.status_code == 201, resp.data

    b = StockBatch.objects.get(batch_number="FUR-2410-A")
    assert b.quantity_remaining == Decimal("250")   # not the old hardcoded 1000
    assert b.supplier_id == supplier.id             # chosen, not Supplier.first()
    assert b.warehouse_id == warehouse.id
    assert b.expiry_date == future
    assert b.movements.count() == 1
    assert "opening_quantity" not in resp.data      # write-only


@pytest.mark.django_db
def test_manual_batch_books_exact_quantity(warehouse, category, unit):
    future = timezone.localdate() + datetime.timedelta(days=400)
    m = create_medicine(**_base(category, unit,
        batch_number="FUR-2410-A", expiry_date=future, opening_quantity=Decimal("250"),
    ))
    b = StockBatch.objects.get(product=m)
    assert b.batch_number == "FUR-2410-A"
    assert b.quantity_remaining == Decimal("250")   # not 1000
    assert b.expiry_date == future
    mv = b.movements.get()
    assert mv.movement_type == MovementType.RECEIPT
    assert mv.quantity_delta == Decimal("250")


@pytest.mark.django_db
def test_zero_quantity_creates_empty_batch_no_movement(warehouse, category, unit):
    future = timezone.localdate() + datetime.timedelta(days=400)
    m = create_medicine(**_base(category, unit,
        batch_number="EMPTY-1", expiry_date=future, opening_quantity=Decimal("0"),
    ))
    b = StockBatch.objects.get(product=m)
    assert b.quantity_remaining == Decimal("0")
    assert b.movements.count() == 0


@pytest.mark.django_db
def test_editing_unrelated_field_does_not_rebook(warehouse, category, unit):
    future = timezone.localdate() + datetime.timedelta(days=400)
    m = create_medicine(**_base(category, unit,
        batch_number="LOT-9", expiry_date=future, opening_quantity=Decimal("40"),
    ))
    update_medicine(m, notes_fr="just a note")
    b = StockBatch.objects.get(product=m, batch_number="LOT-9")
    assert b.quantity_remaining == Decimal("40")  # unchanged
    assert StockBatch.objects.filter(product=m).count() == 1


# ---------------------------------------------------------------------------
# One lot number, one expiry
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_zero_quantity_rejects_conflicting_expiry(warehouse, category, unit):
    """
    A lot code that already exists with a different expiry is a conflict, not
    a match. The quantity path has always refused it; the zero path used to
    match on the lot alone and silently discard the submitted expiry, leaving
    the batch dated differently from the carton it was read off.
    """
    first = timezone.localdate() + datetime.timedelta(days=400)
    second = timezone.localdate() + datetime.timedelta(days=800)

    m = create_medicine(**_base(category, unit,
        batch_number="DUP-1", expiry_date=first, opening_quantity=Decimal("0"),
    ))

    with pytest.raises(BusinessRuleViolation) as caught:
        update_medicine(
            m, batch_number="DUP-1", expiry_date=second,
            opening_quantity=Decimal("0"),
        )
    assert caught.value.code == "batch_expiry_conflict"

    b = StockBatch.objects.get(product=m, batch_number="DUP-1")
    assert b.expiry_date == first  # unchanged, and the caller was told


@pytest.mark.django_db
def test_zero_quantity_same_expiry_is_idempotent(warehouse, category, unit):
    """Re-submitting an identical lot is a no-op, not a duplicate or an error."""
    future = timezone.localdate() + datetime.timedelta(days=400)
    m = create_medicine(**_base(category, unit,
        batch_number="SAME-1", expiry_date=future, opening_quantity=Decimal("0"),
    ))
    update_medicine(
        m, batch_number="SAME-1", expiry_date=future, opening_quantity=Decimal("0"),
    )
    assert StockBatch.objects.filter(product=m, batch_number="SAME-1").count() == 1


# ---------------------------------------------------------------------------
# Half a batch is not a batch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_rejects_batch_without_expiry(
    auth_client, admin_user, warehouse, category, unit
):
    client = auth_client(admin_user)
    resp = client.post("/api/v1/catalog/medicines/", {
        "name": "Half Batch",
        "category": str(category.id), "unit_of_measure": str(unit.id),
        "dosage_form": "TABLET", "unit_cost": "1", "selling_price": "2",
        "batch_number": "NO-EXPIRY",
    }, format="json")
    assert resp.status_code == 400
    body = str(resp.data)
    assert "expiry_date" in body


@pytest.mark.django_db
def test_serializer_rejects_half_a_batch():
    from apps.catalog.models import Category, UnitOfMeasure
    cat = Category.objects.create(code="CS", name_fr="Cat")
    uom = UnitOfMeasure.objects.create(code="US", name_fr="U")
    future = timezone.localdate() + datetime.timedelta(days=400)
    common = {"name": "X", "category": str(cat.id), "unit_of_measure": str(uom.id),
              "unit_cost": "10", "selling_price": "20", "dosage_form": "TABLET"}

    s = MedicineDetailSerializer(data={**common, "batch_number": "B1"})
    s.is_valid()
    assert "expiry_date" in s.errors, s.errors

    s2 = MedicineDetailSerializer(data={**common, "expiry_date": str(future)})
    s2.is_valid()
    assert "batch_number" in s2.errors, s2.errors

    past = timezone.localdate() - datetime.timedelta(days=5)
    s3 = MedicineDetailSerializer(data={**common, "batch_number": "OLD", "expiry_date": str(past)})
    s3.is_valid()
    assert "expiry_date" in s3.errors, s3.errors

    ok = MedicineDetailSerializer(data={**common, "batch_number": "GOOD",
                                        "expiry_date": str(future), "opening_quantity": "5"})
    assert ok.is_valid(), ok.errors
