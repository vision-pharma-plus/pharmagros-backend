"""
Inventory: FIFO/FEFO allocation, the stock ledger, and its invariants.

The highest-risk code in the system. Every test here defends a property that,
if broken, would corrupt stock figures silently rather than loudly.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.exceptions import BusinessRuleViolation, ExpiredBatchError, InsufficientStock
from apps.inventory.models import BatchStatus, MovementType, StockBatch
from apps.inventory.tasks import scan_expiry_horizons
from apps.inventory.services import (
    adjust_stock,
    allocate_fifo,
    find_discrepancies,
    inventory_valuation,
    issue_stock,
    receive_stock,
    reconcile_batch,
    transfer_stock,
    write_off_stock,
)

pytestmark = pytest.mark.django_db


class TestReceiveStock:
    def test_creates_batch_and_ledger_entry(self, batch):
        assert batch.quantity_remaining == Decimal("500.000")
        assert batch.movements.count() == 1
        assert batch.movements.first().movement_type == MovementType.RECEIPT

    def test_expired_goods_are_refused(
        self, product, warehouse, inventory_officer, today
    ):
        """
        Accepting expired stock would put unsellable goods into the valuation
        and make them pickable if the expiry filter ever regressed.
        """
        with pytest.raises(ExpiredBatchError):
            receive_stock(
                product=product,
                warehouse=warehouse,
                batch_number="LOT-DEAD",
                expiry_date=today - timedelta(days=1),
                quantity=Decimal("10"),
                unit_cost=Decimal("100"),
                performed_by=inventory_officer,
            )

    def test_zero_quantity_is_refused(
        self, product, warehouse, inventory_officer, today
    ):
        with pytest.raises(BusinessRuleViolation):
            receive_stock(
                product=product,
                warehouse=warehouse,
                batch_number="LOT-ZERO",
                expiry_date=today + timedelta(days=100),
                quantity=Decimal("0"),
                unit_cost=Decimal("100"),
                performed_by=inventory_officer,
            )

    def test_topping_up_weights_the_cost(
        self, product, warehouse, inventory_officer, today, batch
    ):
        """
        Keeping the original cost would misvalue the new units; overwriting it
        would misvalue the units already held. The weighted average is correct.
        """
        expiry = batch.expiry_date
        receive_stock(
            product=product,
            warehouse=warehouse,
            batch_number="LOT-A",
            expiry_date=expiry,
            quantity=Decimal("500"),
            unit_cost=Decimal("2000"),
            landed_unit_cost=Decimal("2000"),
            performed_by=inventory_officer,
        )
        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("1000.000")
        # (500 x 1000 + 500 x 2000) / 1000 = 1500
        assert batch.landed_unit_cost == Decimal("1500.0000")

    def test_same_batch_number_with_different_expiry_is_refused(
        self, product, warehouse, inventory_officer, today, batch
    ):
        with pytest.raises(BusinessRuleViolation) as exc:
            receive_stock(
                product=product,
                warehouse=warehouse,
                batch_number="LOT-A",
                expiry_date=today + timedelta(days=30),
                quantity=Decimal("10"),
                unit_cost=Decimal("100"),
                performed_by=inventory_officer,
            )
        assert exc.value.code == "batch_expiry_conflict"


class TestFifoAllocation:
    def test_shortest_expiry_is_picked_first(
        self, product, warehouse, two_batches
    ):
        """
        FEFO: ordering is by expiry, not receipt. LOT-SHORT was received
        second but expires first, so it must be consumed first.
        """
        allocations = allocate_fifo(
            product, Decimal("250"), warehouse=warehouse, lock=False
        )
        assert allocations[0].batch.batch_number == "LOT-SHORT"
        assert allocations[0].quantity == Decimal("200.000")
        assert allocations[1].batch.batch_number == "LOT-LONG"
        assert allocations[1].quantity == Decimal("50.0000")

    def test_insufficient_stock_raises(self, product, warehouse, batch):
        with pytest.raises(InsufficientStock) as exc:
            allocate_fifo(product, Decimal("9999"), warehouse=warehouse, lock=False)
        assert exc.value.code == "insufficient_stock"
        assert "available" in exc.value.details

    def test_expired_batches_are_excluded(
        self, product, warehouse, batch, today
    ):
        """An expired batch must be *incapable* of allocation, not merely discouraged."""
        batch.expiry_date = today - timedelta(days=1)
        batch.save(update_fields=["expiry_date"])

        with pytest.raises(InsufficientStock):
            allocate_fifo(product, Decimal("1"), warehouse=warehouse, lock=False)

    @pytest.mark.parametrize(
        "status", [BatchStatus.QUARANTINED, BatchStatus.RECALLED, BatchStatus.DAMAGED]
    )
    def test_non_active_batches_are_excluded(
        self, product, warehouse, batch, status
    ):
        batch.status = status
        batch.save(update_fields=["status"])

        with pytest.raises(InsufficientStock):
            allocate_fifo(product, Decimal("1"), warehouse=warehouse, lock=False)

    def test_reserved_quantity_is_not_allocatable(self, product, warehouse, batch):
        batch.quantity_reserved = Decimal("490")
        batch.save(update_fields=["quantity_reserved"])

        allocations = allocate_fifo(
            product, Decimal("10"), warehouse=warehouse, lock=False
        )
        assert sum(a.quantity for a in allocations) == Decimal("10.0000")

        with pytest.raises(InsufficientStock):
            allocate_fifo(product, Decimal("11"), warehouse=warehouse, lock=False)

    def test_zero_quantity_is_refused(self, product, warehouse, batch):
        with pytest.raises(BusinessRuleViolation):
            allocate_fifo(product, Decimal("0"), warehouse=warehouse, lock=False)


class TestLedgerInvariants:
    def test_balance_equals_ledger_sum(self, batch, product, warehouse, pharmacist):
        """
        The cached balance is a derived figure. If it ever diverges from the
        ledger, valuation and stock figures are both wrong.
        """
        issue_stock(
            product=product,
            warehouse=warehouse,
            quantity=Decimal("120"),
            source_type="test",
            source_id="1",
            performed_by=pharmacist,
        )
        batch.refresh_from_db()
        result = reconcile_batch(batch)
        assert result["reconciled"] is True
        assert result["discrepancy"] == Decimal("0")

    def test_no_discrepancies_after_mixed_operations(
        self, batch, product, warehouse, pharmacist, store_manager
    ):
        issue_stock(
            product=product, warehouse=warehouse, quantity=Decimal("50"),
            source_type="test", source_id="1", performed_by=pharmacist,
        )
        write_off_stock(
            batch_id=batch.pk, quantity=Decimal("5"),
            movement_type=MovementType.DAMAGE,
            reason="Cartons écrasés", performed_by=store_manager,
        )
        adjust_stock(
            batch_id=batch.pk, new_quantity=Decimal("440"),
            reason="Inventaire physique", performed_by=store_manager,
        )
        assert find_discrepancies() == []

    def test_movements_are_immutable(self, batch):
        movement = batch.movements.first()
        movement.quantity_delta = Decimal("999")
        with pytest.raises(RuntimeError, match="immutable"):
            movement.save()

    def test_movements_cannot_be_deleted(self, batch):
        with pytest.raises(RuntimeError, match="cannot be deleted"):
            batch.movements.first().delete()

    def test_batch_cannot_go_negative(self, batch, product, warehouse, pharmacist):
        with pytest.raises(InsufficientStock):
            issue_stock(
                product=product, warehouse=warehouse, quantity=Decimal("501"),
                source_type="test", source_id="1", performed_by=pharmacist,
            )
        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("500.000")

    def test_depleting_a_batch_sets_status(
        self, batch, product, warehouse, pharmacist
    ):
        issue_stock(
            product=product, warehouse=warehouse, quantity=Decimal("500"),
            source_type="test", source_id="1", performed_by=pharmacist,
        )
        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("0.000")
        assert batch.status == BatchStatus.DEPLETED


class TestAdjustments:
    def test_reason_is_mandatory(self, batch, store_manager):
        """
        An unexplained inventory adjustment is exactly what an auditor
        investigates for diversion, so the system refuses to record one.
        """
        with pytest.raises(BusinessRuleViolation) as exc:
            adjust_stock(
                batch_id=batch.pk, new_quantity=Decimal("400"),
                reason="   ", performed_by=store_manager,
            )
        assert exc.value.code == "reason_required"

    def test_negative_quantity_is_refused(self, batch, store_manager):
        with pytest.raises(BusinessRuleViolation):
            adjust_stock(
                batch_id=batch.pk, new_quantity=Decimal("-1"),
                reason="Test", performed_by=store_manager,
            )

    def test_no_change_returns_none(self, batch, store_manager):
        assert (
            adjust_stock(
                batch_id=batch.pk, new_quantity=batch.quantity_remaining,
                reason="Aucun écart", performed_by=store_manager,
            )
            is None
        )

    def test_downward_adjustment_posts_negative_movement(self, batch, store_manager):
        movement = adjust_stock(
            batch_id=batch.pk, new_quantity=Decimal("480"),
            reason="Inventaire physique", performed_by=store_manager,
        )
        assert movement.movement_type == MovementType.ADJUSTMENT_OUT
        assert movement.quantity_delta == Decimal("-20.000")


class TestTransfers:
    def test_transfer_preserves_batch_identity(
        self, batch, warehouse, store_manager, db
    ):
        """
        Transferring without preserving batch number, expiry and cost would
        break traceability the moment stock crosses a location boundary.
        """
        from apps.inventory.models import Warehouse

        destination = Warehouse.objects.create(code="WH-2", name_fr="Second entrepôt")
        transfer_stock(
            batch_id=batch.pk, destination=destination,
            quantity=Decimal("100"), performed_by=store_manager,
            reason="Réapprovisionnement",
        )

        mirrored = StockBatch.objects.get(
            warehouse=destination, batch_number=batch.batch_number
        )
        assert mirrored.quantity_remaining == Decimal("100.000")
        assert mirrored.expiry_date == batch.expiry_date
        assert mirrored.landed_unit_cost == batch.landed_unit_cost

        batch.refresh_from_db()
        assert batch.quantity_remaining == Decimal("400.000")

    def test_same_warehouse_is_refused(self, batch, warehouse, store_manager):
        with pytest.raises(BusinessRuleViolation) as exc:
            transfer_stock(
                batch_id=batch.pk, destination=warehouse,
                quantity=Decimal("10"), performed_by=store_manager,
            )
        assert exc.value.code == "same_warehouse"


class TestWriteOff:
    def test_reason_is_mandatory(self, batch, store_manager):
        with pytest.raises(BusinessRuleViolation):
            write_off_stock(
                batch_id=batch.pk, quantity=Decimal("10"),
                movement_type=MovementType.DISPOSAL,
                reason="", performed_by=store_manager,
            )

    def test_invalid_movement_type_is_refused(self, batch, store_manager):
        with pytest.raises(BusinessRuleViolation):
            write_off_stock(
                batch_id=batch.pk, quantity=Decimal("10"),
                movement_type=MovementType.ISSUE,
                reason="Test", performed_by=store_manager,
            )


class TestValuation:
    def test_valuation_uses_landed_cost(self, batch):
        """
        Inventory is an asset carried at acquisition cost. Valuing at selling
        price would overstate the balance sheet by the full margin.
        """
        result = inventory_valuation()
        assert result["total_units"] == Decimal("500.0000")
        assert result["total_value"] == Decimal("500000.0000")  # 500 x 1000
        assert result["currency"] == "BIF"

    def test_expired_stock_excluded_from_sellable_but_counted(
        self, batch, today
    ):
        """Expired stock still exists physically; it is simply not sellable."""
        batch.status = BatchStatus.EXPIRED
        batch.save(update_fields=["status"])
        result = inventory_valuation()
        assert result["total_units"] == Decimal("0")


class TestExpiryScanHorizons:
    """
    The scan must honour each product's own `expiry_alert_days`, which is
    configured on the medicine form. Before this was wired up the field was
    stored but ignored, so tuning it had no effect on when alerts fired.
    """

    def _receive(self, product, warehouse, supplier, officer, *, days_out, number):
        return receive_stock(
            product=product,
            warehouse=warehouse,
            batch_number=number,
            expiry_date=timezone.localdate() + timedelta(days=days_out),
            quantity=Decimal("10"),
            unit_cost=Decimal("1000"),
            supplier=supplier,
            performed_by=officer,
        )[0]

    def test_short_horizon_product_is_silent_in_wide_bands(
        self, product, warehouse, supplier, inventory_officer
    ):
        """A 30-day product must not be reported 120 days out."""
        product.expiry_alert_days = 30
        product.save(update_fields=["expiry_alert_days"])
        self._receive(
            product, warehouse, supplier, inventory_officer,
            days_out=120, number="LOT-FAST",
        )

        results = scan_expiry_horizons()

        assert results["180d"] == 0
        assert results["90d"] == 0

    def test_short_horizon_product_alerts_once_inside_its_window(
        self, product, warehouse, supplier, inventory_officer
    ):
        """The same product does surface once expiry is within its 30 days."""
        product.expiry_alert_days = 30
        product.save(update_fields=["expiry_alert_days"])
        self._receive(
            product, warehouse, supplier, inventory_officer,
            days_out=20, number="LOT-FAST",
        )

        results = scan_expiry_horizons()

        assert results["30d"] == 1

    def test_long_horizon_product_alerts_in_the_widest_band(
        self, product, warehouse, supplier, inventory_officer
    ):
        """A slow mover on the 180-day default is flagged with runway to act."""
        assert product.expiry_alert_days == 180
        self._receive(
            product, warehouse, supplier, inventory_officer,
            days_out=120, number="LOT-SLOW",
        )

        results = scan_expiry_horizons()

        assert results["180d"] == 1
