from decimal import Decimal

from rest_framework import serializers

from apps.core.fields import MoneySerializerField

from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.product_code", read_only=True)
    product_name = serializers.CharField(source="product.display_name", read_only=True)
    quantity_outstanding = serializers.DecimalField(
        max_digits=18, decimal_places=3, read_only=True,
    )
    is_fully_received = serializers.BooleanField(read_only=True)

    unit_cost = MoneySerializerField()
    discount_amount = MoneySerializerField(read_only=True)
    tax_amount = MoneySerializerField(read_only=True)
    line_total = MoneySerializerField(read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = [
            "id", "line_number", "product", "product_code", "product_name",
            "quantity_ordered", "quantity_received", "quantity_outstanding",
            "is_fully_received",
            "unit_cost", "discount_percent", "discount_amount",
            "tax_rate", "tax_amount", "line_total",
            "expected_expiry_date", "notes",
        ]
        read_only_fields = [
            "id", "line_number", "quantity_received", "discount_amount",
            "tax_amount", "line_total",
        ]


class PurchaseOrderListSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    total_amount = MoneySerializerField(read_only=True)
    receipt_progress = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True,
    )
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "order_number", "status", "supplier", "supplier_name",
            "warehouse_code", "order_date", "expected_delivery_date",
            "total_amount", "currency", "receipt_progress", "is_overdue",
        ]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    supplier_code = serializers.CharField(source="supplier.supplier_code", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    requested_by_name = serializers.CharField(
        source="requested_by.get_full_name", read_only=True, default="",
    )
    approved_by_name = serializers.CharField(
        source="approved_by.get_full_name", read_only=True, default="",
    )

    subtotal = MoneySerializerField(read_only=True)
    discount_amount = MoneySerializerField(read_only=True)
    tax_amount = MoneySerializerField(read_only=True)
    freight_cost = MoneySerializerField()
    customs_duty = MoneySerializerField()
    other_charges = MoneySerializerField()
    total_amount = MoneySerializerField(read_only=True)
    landed_cost_total = MoneySerializerField(read_only=True)

    is_editable = serializers.BooleanField(read_only=True)
    can_receive = serializers.BooleanField(read_only=True)
    is_fully_received = serializers.BooleanField(read_only=True)
    receipt_progress = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True,
    )
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "order_number", "status",
            "supplier", "supplier_code", "supplier_name",
            "warehouse", "warehouse_code",
            "order_date", "expected_delivery_date", "actual_delivery_date",
            "subtotal", "discount_amount", "tax_amount",
            "freight_cost", "customs_duty", "other_charges",
            "total_amount", "landed_cost_total", "currency", "exchange_rate",
            "payment_terms", "supplier_reference",
            "supplier_invoice_number", "supplier_invoice_date",
            "requested_by", "requested_by_name", "submitted_at",
            "approved_by", "approved_by_name", "approved_at",
            "rejection_reason", "sent_at", "cancellation_reason",
            "is_editable", "can_receive", "is_fully_received",
            "receipt_progress", "is_overdue",
            "notes", "lines", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "order_number", "status", "subtotal", "discount_amount",
            "tax_amount", "total_amount", "actual_delivery_date",
            "requested_by", "submitted_at", "approved_by", "approved_at",
            "rejection_reason", "sent_at", "cancellation_reason",
            "created_at", "updated_at",
        ]


class PurchaseOrderLineWriteSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    quantity_ordered = serializers.DecimalField(
        max_digits=18, decimal_places=3, min_value=Decimal("0.001"),
    )
    unit_cost = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0"))
    discount_percent = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=Decimal("0"), max_value=Decimal("100"), required=False,
    )
    tax_rate = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    # Guards against short-dated deliveries: goods arriving inside this date
    # are refused at receipt.
    expected_expiry_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(max_length=255, required=False, allow_blank=True)


class PurchaseOrderCreateSerializer(serializers.Serializer):
    supplier = serializers.UUIDField()
    warehouse = serializers.UUIDField()
    lines = PurchaseOrderLineWriteSerializer(many=True, allow_empty=False)
    expected_delivery_date = serializers.DateField(required=False, allow_null=True)
    freight_cost = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    customs_duty = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    other_charges = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    currency = serializers.CharField(max_length=3, required=False)
    exchange_rate = serializers.DecimalField(
        max_digits=18, decimal_places=6, min_value=Decimal("0.000001"), required=False,
    )
    supplier_reference = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True)


class PurchaseOrderUpdateSerializer(serializers.Serializer):
    """
    Amendments to a draft order.

    Every field is optional — an edit that only moves the delivery date should
    not have to resend the lines. `lines`, when present, replaces the whole
    set; `allow_empty=False` keeps the "an order has at least one line" rule
    true through an edit as well as at creation.
    """

    supplier = serializers.UUIDField(required=False)
    warehouse = serializers.UUIDField(required=False)
    lines = PurchaseOrderLineWriteSerializer(
        many=True, allow_empty=False, required=False,
    )
    expected_delivery_date = serializers.DateField(required=False, allow_null=True)
    freight_cost = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    customs_duty = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    other_charges = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    currency = serializers.CharField(max_length=3, required=False)
    exchange_rate = serializers.DecimalField(
        max_digits=18, decimal_places=6, min_value=Decimal("0.000001"), required=False,
    )
    supplier_reference = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True)


class ApprovalSerializer(serializers.Serializer):
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RejectionSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class GoodsReceiptLineWriteSerializer(serializers.Serializer):
    # Line models use an integer primary key (they are child rows, not
    # independently addressable business entities like the order header,
    # which uses a UUID). Declaring this as a UUIDField silently coerced the
    # value and made every receipt fail line lookup.
    purchase_order_line = serializers.IntegerField()
    batch_number = serializers.CharField(max_length=64)
    expiry_date = serializers.DateField()
    manufacturing_date = serializers.DateField(required=False, allow_null=True)
    quantity_received = serializers.DecimalField(
        max_digits=18, decimal_places=3, min_value=Decimal("0.001"),
    )
    quantity_rejected = serializers.DecimalField(
        max_digits=18, decimal_places=3, min_value=Decimal("0"), required=False,
    )
    rejection_reason = serializers.CharField(
        max_length=255, required=False, allow_blank=True,
    )
    unit_cost = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )


class GoodsReceiptCreateSerializer(serializers.Serializer):
    lines = GoodsReceiptLineWriteSerializer(many=True, allow_empty=False)
    delivery_note_number = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
    )
    receipt_date = serializers.DateTimeField(required=False)
    quality_checked = serializers.BooleanField(default=False)
    quality_notes = serializers.CharField(required=False, allow_blank=True)


class GoodsReceiptLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    unit_cost = MoneySerializerField(read_only=True)
    landed_unit_cost = MoneySerializerField(read_only=True)

    class Meta:
        model = GoodsReceiptLine
        fields = [
            "id", "purchase_order_line", "product", "product_name", "batch",
            "batch_number", "manufacturing_date", "expiry_date",
            "quantity_received", "quantity_rejected", "rejection_reason",
            "unit_cost", "landed_unit_cost",
        ]
        read_only_fields = fields


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = GoodsReceiptLineSerializer(many=True, read_only=True)
    order_number = serializers.CharField(
        source="purchase_order.order_number", read_only=True,
    )
    supplier_name = serializers.CharField(
        source="purchase_order.supplier.name", read_only=True,
    )
    received_by_name = serializers.CharField(
        source="received_by.get_full_name", read_only=True, default="",
    )

    class Meta:
        model = GoodsReceipt
        fields = [
            "id", "receipt_number", "purchase_order", "order_number", "supplier_name",
            "warehouse", "receipt_date", "delivery_note_number",
            "received_by", "received_by_name",
            "quality_checked", "quality_checked_by", "quality_notes",
            "notes", "lines", "created_at",
        ]
        read_only_fields = fields


class SupplierInvoiceSerializer(serializers.Serializer):
    invoice_number = serializers.CharField(max_length=64)
    invoice_date = serializers.DateField()
