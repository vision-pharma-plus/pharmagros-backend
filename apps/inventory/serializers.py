from decimal import Decimal

from rest_framework import serializers

from apps.core.fields import MoneySerializerField

from .models import MovementType, StockBatch, StockMovement, Warehouse


class WarehouseSerializer(serializers.ModelSerializer):
    # Resolved for the active language; the _fr/_en pair stays writable so an
    # administrator can correct either translation.
    name = serializers.CharField(read_only=True)

    class Meta:
        model = Warehouse
        fields = [
            "id", "code", "name", "name_fr", "name_en", "address", "city",
            "is_default", "is_active", "is_cold_chain",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class StockBatchSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.product_code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")

    quantity_available = serializers.DecimalField(
        max_digits=18, decimal_places=3, read_only=True,
    )
    stock_value = MoneySerializerField(read_only=True)
    unit_cost = MoneySerializerField(read_only=True)
    landed_unit_cost = MoneySerializerField(read_only=True)

    is_expired = serializers.BooleanField(read_only=True)
    days_to_expiry = serializers.IntegerField(read_only=True)

    class Meta:
        model = StockBatch
        fields = [
            "id", "product", "product_code", "product_name",
            "warehouse", "warehouse_code", "batch_number",
            "manufacturing_date", "expiry_date", "days_to_expiry", "is_expired",
            "supplier", "supplier_name", "purchase_order",
            "quantity_received", "quantity_remaining", "quantity_reserved",
            "quantity_available",
            "unit_cost", "landed_unit_cost", "stock_value",
            "status", "received_at", "notes",
            "created_at", "updated_at",
        ]
        # Quantities are only ever changed by posting a stock movement, never
        # by editing the batch. Exposing them as writable would allow the
        # ledger and the balance to diverge silently.
        read_only_fields = [
            "id", "quantity_received", "quantity_remaining", "quantity_reserved",
            "unit_cost", "landed_unit_cost", "received_at",
            "created_at", "updated_at",
        ]


class StockMovementSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.product_code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    movement_type_display = serializers.CharField(
        source="get_movement_type_display", read_only=True,
    )
    performed_by_name = serializers.CharField(
        source="performed_by.get_full_name", read_only=True, default="",
    )
    unit_cost = MoneySerializerField(read_only=True)
    total_value = MoneySerializerField(read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id", "batch", "batch_number", "product", "product_code", "product_name",
            "warehouse", "warehouse_code",
            "movement_type", "movement_type_display",
            "quantity_delta", "balance_after", "unit_cost", "total_value",
            "source_type", "source_id", "source_reference",
            "performed_by", "performed_by_name", "performed_at",
            "reason", "notes",
        ]
        # The ledger is append-only; the API offers no write path at all.
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Operation payloads
# ---------------------------------------------------------------------------


class ReceiveStockSerializer(serializers.Serializer):
    """Direct stock receipt, outside the purchase-order flow."""

    product = serializers.UUIDField()
    warehouse = serializers.UUIDField()
    batch_number = serializers.CharField(max_length=64)
    expiry_date = serializers.DateField()
    manufacturing_date = serializers.DateField(required=False, allow_null=True)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))
    unit_cost = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0"))
    landed_unit_cost = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    supplier = serializers.UUIDField(required=False, allow_null=True)
    source_reference = serializers.CharField(max_length=64, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_expiry_date(self, value):
        from django.utils import timezone

        if value < timezone.localdate():
            raise serializers.ValidationError(
                "Expired stock cannot be received. Verify the expiry date."
            )
        return value


class BulkReceiveLineSerializer(ReceiveStockSerializer):
    """
    One line of a multi-product delivery.

    Inherits every rule from the single-line receipt — most importantly that
    `batch_number` and `expiry_date` are unconditionally required. A delivery
    arriving without a lot code on the carton is not receivable, and that is
    true whether it is booked alone or among eleven others.
    """


class BulkReceiveStockSerializer(serializers.Serializer):
    """
    A whole delivery booked in one transaction.

    Exists because a receiving clerk unpacks one shipment, not one product:
    posting the lines one request at a time would let a failure on line 9
    leave lines 1-8 committed, with the clerk holding a half-booked delivery
    and no obvious way to finish it. The stock ledger is the wrong place to
    discover a partial write.
    """

    lines = BulkReceiveLineSerializer(many=True, allow_empty=False)
    source_reference = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
    )
    # Distinguishes go-live stock from an ordinary delivery. Opening balances
    # are asserted from a physical count rather than received from a supplier,
    # and a valuation or purchase report that conflated the two would be wrong.
    is_opening_balance = serializers.BooleanField(default=False)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_lines(self, value):
        """
        Reject the same lot twice in one delivery.

        Two lines for one product/lot/warehouse would weighted-average against
        a balance the first line had just written, so the clerk's second cost
        would silently move the first. Merging them is the operator's decision
        to make on the form, where they can see both quantities.
        """
        seen = set()
        for index, line in enumerate(value):
            key = (line["product"], line["warehouse"], line["batch_number"].strip())
            if key in seen:
                raise serializers.ValidationError(
                    {
                        index: (
                            "This product, lot and warehouse combination is already "
                            "on another line. Combine them into a single line."
                        )
                    }
                )
            seen.add(key)
        return value


class AdjustStockSerializer(serializers.Serializer):
    """Stocktake correction. A reason is mandatory."""

    new_quantity = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0"))
    reason = serializers.CharField(max_length=255)
    notes = serializers.CharField(required=False, allow_blank=True)
    source_reference = serializers.CharField(max_length=64, required=False, allow_blank=True)


class TransferStockSerializer(serializers.Serializer):
    destination = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)
    source_reference = serializers.CharField(max_length=64, required=False, allow_blank=True)


class WriteOffStockSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))
    movement_type = serializers.ChoiceField(
        choices=[MovementType.DAMAGE, MovementType.EXPIRY, MovementType.DISPOSAL]
    )
    reason = serializers.CharField(max_length=255)
    source_reference = serializers.CharField(max_length=64, required=False, allow_blank=True)


class StockLevelSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    product_code = serializers.CharField()
    product_name = serializers.CharField()
    reorder_level = serializers.DecimalField(max_digits=18, decimal_places=3)
    quantity_on_hand = serializers.DecimalField(max_digits=18, decimal_places=3)
    quantity_available = serializers.DecimalField(max_digits=18, decimal_places=3)
    batch_count = serializers.IntegerField()
    is_low = serializers.BooleanField()
    is_out = serializers.BooleanField()


class ValuationSerializer(serializers.Serializer):
    total_value = MoneySerializerField()
    total_units = serializers.DecimalField(max_digits=18, decimal_places=4)
    distinct_products = serializers.IntegerField()
    batch_count = serializers.IntegerField()
    as_of = serializers.DateTimeField()
    currency = serializers.CharField()


class ReconciliationSerializer(serializers.Serializer):
    batch_id = serializers.CharField()
    batch_number = serializers.CharField()
    cached_balance = serializers.DecimalField(max_digits=18, decimal_places=3)
    ledger_balance = serializers.DecimalField(max_digits=18, decimal_places=3)
    discrepancy = serializers.DecimalField(max_digits=18, decimal_places=4)
    reconciled = serializers.BooleanField()
