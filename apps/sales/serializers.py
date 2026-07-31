from decimal import Decimal

from rest_framework import serializers

from apps.core.fields import MoneySerializerField

from .models import Sale, SaleLine, SaleLineBatch, SaleReturn, SaleReturnLine, SaleType


class SaleLineBatchSerializer(serializers.ModelSerializer):
    unit_cost = MoneySerializerField(read_only=True)

    class Meta:
        model = SaleLineBatch
        fields = [
            "id", "batch", "batch_number", "expiry_date",
            "quantity", "quantity_returned", "unit_cost",
        ]
        read_only_fields = fields


class SaleLineSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.product_code", read_only=True)
    product_name = serializers.CharField(source="product.display_name", read_only=True)
    batch_allocations = SaleLineBatchSerializer(many=True, read_only=True)
    quantity_net = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)

    unit_price = MoneySerializerField()
    line_total = MoneySerializerField(read_only=True)
    line_subtotal = MoneySerializerField(read_only=True)
    discount_amount = MoneySerializerField(read_only=True)
    tax_amount = MoneySerializerField(read_only=True)

    class Meta:
        model = SaleLine
        fields = [
            "id", "line_number", "product", "product_code", "product_name",
            "quantity", "quantity_returned", "quantity_net",
            "unit_price", "discount_percent", "discount_amount",
            "tax_rate", "tax_amount", "line_subtotal", "line_total",
            "batch_allocations",
        ]
        # Cost is margin data, not customer-facing; it is exposed only through
        # the margin endpoint, which requires sales.view_margin.
        read_only_fields = [
            "id", "line_number", "quantity_returned", "discount_amount",
            "tax_rate", "tax_amount", "line_subtotal", "line_total",
        ]


class SaleLineWriteSerializer(serializers.Serializer):
    """Input shape for creating sale lines."""

    product = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))
    unit_price = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    discount_percent = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=Decimal("0"), max_value=Decimal("100"), required=False,
    )


class SaleListSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.business_name", read_only=True)
    customer_code = serializers.CharField(source="customer.customer_code", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    invoice_number = serializers.CharField(
        source="invoice.invoice_number", read_only=True, default=None,
    )
    total_amount = MoneySerializerField(read_only=True)

    class Meta:
        model = Sale
        fields = [
            "id", "sale_number", "sale_type", "status",
            "customer", "customer_code", "customer_name", "warehouse_code",
            "sale_date", "total_amount", "invoice_number",
        ]


class SaleSerializer(serializers.ModelSerializer):
    lines = SaleLineSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.business_name", read_only=True)
    customer_code = serializers.CharField(source="customer.customer_code", read_only=True)
    salesperson_name = serializers.CharField(
        source="salesperson.get_full_name", read_only=True, default="",
    )
    invoice_number = serializers.CharField(
        source="invoice.invoice_number", read_only=True, default=None,
    )
    is_editable = serializers.BooleanField(read_only=True)

    subtotal = MoneySerializerField(read_only=True)
    discount_amount = MoneySerializerField(read_only=True)
    tax_amount = MoneySerializerField(read_only=True)
    total_amount = MoneySerializerField(read_only=True)

    class Meta:
        model = Sale
        fields = [
            "id", "sale_number", "sale_type", "status",
            "customer", "customer_code", "customer_name",
            "warehouse", "sale_date",
            "subtotal", "discount_percent", "discount_amount",
            "tax_amount", "total_amount",
            "salesperson", "salesperson_name",
            "delivery_address", "delivery_note_number", "customer_order_reference",
            "credit_override_reason", "confirmed_at",
            "cancelled_at", "cancellation_reason",
            "invoice_number", "is_editable", "notes", "lines",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "sale_number", "status", "subtotal", "discount_amount",
            "tax_amount", "total_amount", "confirmed_at", "cancelled_at",
            "cancellation_reason", "credit_override_reason",
            "created_at", "updated_at",
        ]


class SaleCreateSerializer(serializers.Serializer):
    customer = serializers.UUIDField()
    warehouse = serializers.UUIDField()
    sale_type = serializers.ChoiceField(choices=SaleType.choices, default=SaleType.CASH)
    lines = SaleLineWriteSerializer(many=True, allow_empty=False)
    sale_date = serializers.DateTimeField(required=False)
    discount_percent = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=Decimal("0"), max_value=Decimal("100"), required=False,
    )
    customer_order_reference = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True)


class ConfirmSaleSerializer(serializers.Serializer):
    """
    Confirmation payload.

    A credit-limit override requires both a reason and an authorising user —
    an override is a deliberate assumption of risk and must be attributable.
    """

    generate_invoice = serializers.BooleanField(default=True)
    credit_override_reason = serializers.CharField(
        max_length=255, required=False, allow_blank=True,
    )


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class SaleReturnLineWriteSerializer(serializers.Serializer):
    # SaleLine has an integer primary key; only the Sale header is a UUID.
    sale_line = serializers.IntegerField()
    batch = serializers.UUIDField(required=False, allow_null=True)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))
    # Deliberately defaults to False: returned medicines re-enter sellable
    # stock only when storage integrity was demonstrably maintained.
    restock = serializers.BooleanField(default=False)
    condition_notes = serializers.CharField(max_length=255, required=False, allow_blank=True)


class SaleReturnCreateSerializer(serializers.Serializer):
    lines = SaleReturnLineWriteSerializer(many=True, allow_empty=False)
    reason = serializers.CharField(max_length=255)
    issue_credit_note = serializers.BooleanField(default=True)


class SaleReturnLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="sale_line.product.name", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    unit_price = MoneySerializerField(read_only=True)
    refund_amount = MoneySerializerField(read_only=True)

    class Meta:
        model = SaleReturnLine
        fields = [
            "id", "sale_line", "product_name", "batch", "batch_number",
            "quantity", "unit_price", "refund_amount", "restock", "condition_notes",
        ]
        read_only_fields = fields


class SaleReturnSerializer(serializers.ModelSerializer):
    lines = SaleReturnLineSerializer(many=True, read_only=True)
    sale_number = serializers.CharField(source="sale.sale_number", read_only=True)
    customer_name = serializers.CharField(source="customer.business_name", read_only=True)
    credit_note_number = serializers.CharField(
        source="credit_note.invoice_number", read_only=True, default=None,
    )
    total_amount = MoneySerializerField(read_only=True)

    class Meta:
        model = SaleReturn
        fields = [
            "id", "return_number", "sale", "sale_number",
            "customer", "customer_name", "return_date", "reason",
            "total_amount", "credit_note", "credit_note_number",
            "processed_by", "notes", "lines", "created_at",
        ]
        read_only_fields = fields


class RecallTraceSerializer(serializers.Serializer):
    sale_number = serializers.CharField()
    sale_date = serializers.DateTimeField()
    customer_code = serializers.CharField()
    customer_name = serializers.CharField()
    customer_phone = serializers.CharField()
    customer_email = serializers.CharField()
    product = serializers.CharField()
    batch_number = serializers.CharField()
    expiry_date = serializers.DateField()
    quantity_supplied = serializers.DecimalField(max_digits=18, decimal_places=3)
    quantity_returned = serializers.DecimalField(max_digits=18, decimal_places=3)
    quantity_outstanding = serializers.DecimalField(max_digits=18, decimal_places=3)


class SaleMarginSerializer(serializers.Serializer):
    sale_number = serializers.CharField()
    revenue_net_of_tax = MoneySerializerField()
    cost_of_goods = MoneySerializerField()
    gross_margin = MoneySerializerField()
    gross_margin_percent = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.CharField()
