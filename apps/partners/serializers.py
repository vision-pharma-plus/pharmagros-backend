from decimal import Decimal

from rest_framework import serializers

from apps.core.fields import MoneySerializerField
from apps.core.validators import validate_nif

from .models import CreditLimitChange, Customer, CustomerContact, Supplier


class CustomerListSerializer(serializers.ModelSerializer):
    """Slim payload for list screens."""

    credit_limit = MoneySerializerField(read_only=True)
    outstanding_balance = MoneySerializerField(read_only=True)
    available_credit = MoneySerializerField(read_only=True)
    is_over_limit = serializers.BooleanField(read_only=True)
    # Included in the list payload because the sales screen must warn about a
    # lapsed operating licence *before* an order is started — discovering it
    # only on the detail record would waste the operator's work.
    licence_is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id", "customer_code", "business_name", "customer_type", "nif",
            "city", "phone", "email", "status",
            "credit_limit", "outstanding_balance", "available_credit",
            "credit_blocked", "is_over_limit", "licence_is_expired",
            "payment_terms",
        ]


class CustomerSerializer(serializers.ModelSerializer):
    available_credit = MoneySerializerField(read_only=True)
    credit_utilisation = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True,
    )
    is_over_limit = serializers.BooleanField(read_only=True)
    is_institutional = serializers.BooleanField(read_only=True)
    licence_is_expired = serializers.BooleanField(read_only=True)
    payment_term_days = serializers.IntegerField(read_only=True)

    credit_limit = MoneySerializerField(required=False)
    outstanding_balance = MoneySerializerField(read_only=True)

    # Credit limit changes must be justified and are routed through the
    # service layer, so the reason is accepted here but never stored on the
    # customer row itself.
    credit_limit_reason = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
    )

    class Meta:
        model = Customer
        fields = [
            "id", "customer_code", "business_name", "trading_name", "customer_type",
            "nif", "rc_number", "pharmacy_licence", "licence_expiry", "licence_is_expired",
            "contact_person", "email", "phone", "alternate_phone",
            "address", "city", "province", "country",
            "credit_limit", "outstanding_balance", "available_credit",
            "credit_utilisation", "is_over_limit", "payment_terms", "payment_term_days",
            "credit_blocked", "credit_block_reason", "discount_percent",
            "is_institutional", "status", "notes",
            "first_sale_at", "last_sale_at",
            "credit_limit_reason",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "customer_code", "outstanding_balance", "credit_blocked",
            "credit_block_reason", "first_sale_at", "last_sale_at",
            "created_at", "updated_at",
        ]

    def validate_nif(self, value):
        if value:
            validate_nif(value)
        return value

    def validate(self, attrs):
        """
        A customer on credit terms must be identifiable to the tax authority.

        Checked here as well as in the service so the API returns a proper
        field-level validation error rather than a generic 422.
        """
        from .models import PaymentTerms

        terms = attrs.get(
            "payment_terms", getattr(self.instance, "payment_terms", PaymentTerms.CASH)
        )
        nif = attrs.get("nif", getattr(self.instance, "nif", ""))

        if terms != PaymentTerms.CASH and not nif:
            raise serializers.ValidationError(
                {"nif": "A NIF is required for a customer with credit payment terms."}
            )
        return attrs


class CustomerContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerContact
        fields = ["id", "customer", "name", "role", "email", "phone", "is_primary"]
        read_only_fields = ["id"]


class CreditLimitChangeSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(
        source="changed_by.get_full_name", read_only=True, default="",
    )
    old_limit = MoneySerializerField(read_only=True)
    new_limit = MoneySerializerField(read_only=True)

    class Meta:
        model = CreditLimitChange
        fields = [
            "id", "customer", "old_limit", "new_limit", "reason",
            "changed_by", "changed_by_name", "created_at",
        ]
        read_only_fields = fields


class SetCreditLimitSerializer(serializers.Serializer):
    credit_limit = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0"))
    reason = serializers.CharField(max_length=255)


class CreditBlockSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class StatementLineSerializer(serializers.Serializer):
    date = serializers.DateTimeField()
    type = serializers.CharField()
    reference = serializers.CharField()
    debit = MoneySerializerField()
    credit = MoneySerializerField()
    balance = MoneySerializerField()
    due_date = serializers.DateField(allow_null=True)


class CustomerStatementSerializer(serializers.Serializer):
    customer_code = serializers.CharField()
    business_name = serializers.CharField()
    date_from = serializers.DateTimeField(allow_null=True)
    date_to = serializers.DateTimeField()
    lines = StatementLineSerializer(many=True)
    total_invoiced = MoneySerializerField()
    total_paid = MoneySerializerField()
    closing_balance = MoneySerializerField()
    currency = serializers.CharField()


class SupplierSerializer(serializers.ModelSerializer):
    payment_term_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = Supplier
        fields = [
            "id", "supplier_code", "name", "nif",
            "contact_person", "email", "phone", "address", "city", "country",
            "payment_terms", "payment_term_days", "currency", "lead_time_days",
            "bank_name", "bank_account", "swift_code",
            "status", "is_approved", "approval_notes", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "supplier_code", "is_approved", "approval_notes",
            "created_at", "updated_at",
        ]


class SupplierApprovalSerializer(serializers.Serializer):
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True)


class SupplierPerformanceSerializer(serializers.Serializer):
    total_orders = serializers.IntegerField()
    received_orders = serializers.IntegerField()
    on_time_deliveries = serializers.IntegerField()
    late_deliveries = serializers.IntegerField()
    on_time_rate = serializers.DecimalField(max_digits=10, decimal_places=4)
    average_delay_days = serializers.DecimalField(max_digits=10, decimal_places=4)
    total_purchase_value = MoneySerializerField()
    currency = serializers.CharField()
