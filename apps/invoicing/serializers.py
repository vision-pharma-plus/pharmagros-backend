from decimal import Decimal

from rest_framework import serializers

from apps.core.fields import MoneySerializerField

from .models import Invoice, InvoiceLine, Payment, PaymentAllocation, PaymentMethod


class InvoiceLineSerializer(serializers.ModelSerializer):
    unit_price = MoneySerializerField(read_only=True)
    discount_amount = MoneySerializerField(read_only=True)
    tax_amount = MoneySerializerField(read_only=True)
    line_subtotal = MoneySerializerField(read_only=True)
    line_total = MoneySerializerField(read_only=True)

    class Meta:
        model = InvoiceLine
        fields = [
            "id", "line_number", "product", "product_code", "description",
            "batch_numbers", "expiry_dates",
            "quantity", "unit_of_measure", "unit_price",
            "discount_percent", "discount_amount",
            "tax_rate", "tax_amount", "line_subtotal", "line_total",
        ]
        # unit_cost is deliberately excluded: it is margin data and must not
        # reach a customer-facing document or an unprivileged client.
        read_only_fields = fields


class InvoiceListSerializer(serializers.ModelSerializer):
    total_amount = MoneySerializerField(read_only=True)
    balance_due = MoneySerializerField(read_only=True)
    paid_amount = MoneySerializerField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    days_overdue = serializers.IntegerField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "invoice_number", "invoice_type", "status",
            "customer", "customer_name", "customer_nif",
            "invoice_date", "due_date", "is_credit_sale",
            "total_amount", "paid_amount", "balance_due",
            "is_overdue", "days_overdue",
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    customer_code = serializers.CharField(source="customer.customer_code", read_only=True)
    sale_number = serializers.CharField(source="sale.sale_number", read_only=True, default=None)
    original_invoice_number = serializers.CharField(
        source="original_invoice.invoice_number", read_only=True, default=None,
    )
    posted_by_name = serializers.CharField(
        source="posted_by.get_full_name", read_only=True, default="",
    )

    subtotal = MoneySerializerField(read_only=True)
    discount_amount = MoneySerializerField(read_only=True)
    taxable_amount = MoneySerializerField(read_only=True)
    tax_amount = MoneySerializerField(read_only=True)
    total_amount = MoneySerializerField(read_only=True)
    paid_amount = MoneySerializerField(read_only=True)
    balance_due = MoneySerializerField(read_only=True)

    is_editable = serializers.BooleanField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    days_overdue = serializers.IntegerField(read_only=True)
    payment_progress = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True,
    )

    class Meta:
        model = Invoice
        fields = [
            "id", "invoice_number", "invoice_type", "status",
            "customer", "customer_code",
            "customer_name", "customer_nif", "customer_address", "customer_phone",
            "sale", "sale_number",
            "invoice_date", "due_date", "payment_terms_days",
            "subtotal", "discount_amount", "taxable_amount", "tax_amount",
            "total_amount", "paid_amount", "balance_due", "currency",
            "is_credit_sale", "reference", "notes",
            "posted_at", "posted_by", "posted_by_name",
            "cancelled_at", "cancellation_reason",
            "print_count", "last_printed_at", "emailed_at",
            "original_invoice", "original_invoice_number",
            "is_editable", "is_overdue", "days_overdue", "payment_progress",
            "lines", "created_at", "updated_at",
        ]
        read_only_fields = fields


class InvoiceLineWriteSerializer(serializers.Serializer):
    product = serializers.UUIDField(required=False, allow_null=True)
    description = serializers.CharField(max_length=255)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0"))
    discount_percent = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=Decimal("0"), max_value=Decimal("100"), required=False,
    )
    tax_rate = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    batch_numbers = serializers.CharField(max_length=255, required=False, allow_blank=True)
    unit_of_measure = serializers.CharField(max_length=16, required=False, allow_blank=True)


class InvoiceCreateSerializer(serializers.Serializer):
    customer = serializers.UUIDField()
    lines = InvoiceLineWriteSerializer(many=True, allow_empty=False)
    is_credit_sale = serializers.BooleanField(default=False)
    invoice_date = serializers.DateTimeField(required=False)
    reference = serializers.CharField(max_length=64, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class CreditNoteSerializer(serializers.Serializer):
    lines = InvoiceLineWriteSerializer(many=True, allow_empty=False)
    reason = serializers.CharField(max_length=255)


class CancelInvoiceSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class PaymentAllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    amount = MoneySerializerField(read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ["id", "invoice", "invoice_number", "amount", "created_at"]
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.business_name", read_only=True)
    customer_code = serializers.CharField(source="customer.customer_code", read_only=True)
    allocations = PaymentAllocationSerializer(many=True, read_only=True)
    received_by_name = serializers.CharField(
        source="received_by.get_full_name", read_only=True, default="",
    )

    amount = MoneySerializerField(read_only=True)
    allocated_amount = MoneySerializerField(read_only=True)
    unallocated_amount = MoneySerializerField(read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id", "reference", "customer", "customer_code", "customer_name",
            "payment_date", "amount", "allocated_amount", "unallocated_amount",
            "method", "bank_reference", "received_by", "received_by_name",
            "notes", "is_reversed", "reversal_reason",
            "allocations", "created_at",
        ]
        read_only_fields = fields


class PaymentCreateSerializer(serializers.Serializer):
    customer = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0.0001"))
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    payment_date = serializers.DateTimeField(required=False)
    bank_reference = serializers.CharField(max_length=64, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    # When omitted, allocation is oldest-due-first — the standard commercial
    # convention and the one that minimises the customer's ageing profile.
    invoice_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True,
    )


class ReversePaymentSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)
