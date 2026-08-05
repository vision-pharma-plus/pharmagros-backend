from decimal import Decimal

from rest_framework import serializers

from apps.core.fields import MoneySerializerField

from .models import Expense, ExpenseCategory, ExpensePaymentMethod, ExpenseStatus


class ExpenseCategorySerializer(serializers.ModelSerializer):
    """
    An expense category, with both language variants.

    `name` and `description` are the resolved, read-only values for the
    request's language; `name_fr`/`name_en` are the writable columns behind
    them. Same shape as `catalog.CategorySerializer`, so a client that knows
    one knows the other.
    """

    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    expense_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ExpenseCategory
        fields = [
            "id", "code", "name", "name_fr", "name_en",
            "description", "description_fr", "description_en",
            "is_active", "expense_count",
        ]
        read_only_fields = ["id", "expense_count"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `name_fr` is non-blank on the model, but a client may legitimately
        # supply only `name_en` — `save()` back-fills the other side. Same
        # accommodation the catalogue makes.
        self.fields["name_fr"].required = False


class ExpenseSerializer(serializers.ModelSerializer):
    category_code = serializers.CharField(source="category.code", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    supplier_name = serializers.CharField(
        source="supplier.name", read_only=True, default=None,
    )
    order_number = serializers.CharField(
        source="purchase_order.order_number", read_only=True, default=None,
    )
    recorded_by_name = serializers.CharField(
        source="recorded_by.get_full_name", read_only=True, default="",
    )
    approved_by_name = serializers.CharField(
        source="approved_by.get_full_name", read_only=True, default="",
    )

    amount = MoneySerializerField(read_only=True)
    tax_amount = MoneySerializerField(read_only=True)
    net_amount = MoneySerializerField(read_only=True)

    is_editable = serializers.BooleanField(read_only=True)
    is_paid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id", "reference", "status",
            "category", "category_code", "category_name",
            "description", "notes",
            "expense_date", "paid_date",
            "amount", "tax_amount", "net_amount", "currency",
            "payment_method", "payment_reference",
            "payee", "supplier", "supplier_name",
            "purchase_order", "order_number", "receipt_number",
            "recorded_by", "recorded_by_name",
            "approved_by", "approved_by_name", "approved_at",
            "cancelled_at", "cancellation_reason",
            "is_editable", "is_paid",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class ExpenseListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_code = serializers.CharField(source="category.code", read_only=True)
    amount = MoneySerializerField(read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id", "reference", "status", "category", "category_code", "category_name",
            "description", "expense_date", "paid_date",
            "amount", "currency", "payment_method", "payee",
        ]
        read_only_fields = fields


class ExpenseWriteSerializer(serializers.Serializer):
    """
    Input for recording or amending an expense.

    Every field except the essentials is optional so the same shape serves
    both create and partial update; `ExpenseViewSet` marks which are required
    on create.
    """

    category = serializers.UUIDField()
    description = serializers.CharField(max_length=255)
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001"),
    )
    tax_amount = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    expense_date = serializers.DateField(required=False)
    paid_date = serializers.DateField(required=False, allow_null=True)
    payment_method = serializers.ChoiceField(
        choices=ExpensePaymentMethod.choices, default=ExpensePaymentMethod.CASH,
    )
    payment_reference = serializers.CharField(max_length=64, required=False, allow_blank=True)
    payee = serializers.CharField(max_length=200, required=False, allow_blank=True)
    supplier = serializers.UUIDField(required=False, allow_null=True)
    purchase_order = serializers.UUIDField(required=False, allow_null=True)
    receipt_number = serializers.CharField(max_length=64, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    currency = serializers.CharField(max_length=3, required=False)
    status = serializers.ChoiceField(choices=ExpenseStatus.choices, required=False)


class ExpenseUpdateSerializer(ExpenseWriteSerializer):
    """Amendment payload: everything optional, since only changes are sent."""

    category = serializers.UUIDField(required=False)
    description = serializers.CharField(max_length=255, required=False)
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=Decimal("0.0001"), required=False,
    )
    payment_method = serializers.ChoiceField(
        choices=ExpensePaymentMethod.choices, required=False,
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class MarkPaidSerializer(serializers.Serializer):
    paid_date = serializers.DateField(required=False)
    payment_method = serializers.ChoiceField(
        choices=ExpensePaymentMethod.choices, required=False,
    )
    payment_reference = serializers.CharField(max_length=64, required=False, allow_blank=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class OptionalReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Report response shapes
#
# Declared explicitly so the OpenAPI schema documents them, and so the frontend
# has generated types rather than guessing at the payload.
# ---------------------------------------------------------------------------


class ExpenseCategoryReportRowSerializer(serializers.Serializer):
    category_id = serializers.CharField()
    category_code = serializers.CharField()
    category_name = serializers.CharField()
    category_name_fr = serializers.CharField()
    category_name_en = serializers.CharField()
    expense_count = serializers.IntegerField()
    total_amount = MoneySerializerField()
    total_tax = MoneySerializerField()
    percentage = serializers.DecimalField(max_digits=10, decimal_places=2)


class ExpenseCategoryReportSerializer(serializers.Serializer):
    date_from = serializers.DateField(allow_null=True)
    date_to = serializers.DateField(allow_null=True)
    total_amount = MoneySerializerField()
    expense_count = serializers.IntegerField()
    category_count = serializers.IntegerField()
    categories = ExpenseCategoryReportRowSerializer(many=True)


class SupplierPaymentReportRowSerializer(serializers.Serializer):
    supplier_id = serializers.CharField()
    supplier_code = serializers.CharField()
    supplier_name = serializers.CharField()
    payment_count = serializers.IntegerField()
    total_paid = MoneySerializerField()
    total_allocated = MoneySerializerField()


class PaymentMethodRowSerializer(serializers.Serializer):
    method = serializers.CharField()
    payment_count = serializers.IntegerField()
    total_paid = MoneySerializerField()


class SupplierPaymentReportSerializer(serializers.Serializer):
    date_from = serializers.DateField(allow_null=True)
    date_to = serializers.DateField(allow_null=True)
    total_paid = MoneySerializerField()
    total_allocated = MoneySerializerField()
    total_unallocated = MoneySerializerField()
    payment_count = serializers.IntegerField()
    reversed_count = serializers.IntegerField()
    suppliers = SupplierPaymentReportRowSerializer(many=True)
    methods = PaymentMethodRowSerializer(many=True)


class AgeingBucketSerializer(serializers.Serializer):
    current = MoneySerializerField()
    days_1_30 = MoneySerializerField()
    days_31_60 = MoneySerializerField()
    days_61_90 = MoneySerializerField()
    days_over_90 = MoneySerializerField()


class OutstandingInvoiceRowSerializer(serializers.Serializer):
    id = serializers.CharField()
    reference = serializers.CharField()
    invoice_number = serializers.CharField()
    invoice_date = serializers.DateField()
    due_date = serializers.DateField(allow_null=True)
    total_amount = MoneySerializerField()
    paid_amount = MoneySerializerField()
    balance_due = MoneySerializerField()
    days_overdue = serializers.IntegerField()
    payment_progress = serializers.DecimalField(max_digits=10, decimal_places=2)


class OutstandingSupplierRowSerializer(serializers.Serializer):
    supplier_id = serializers.CharField()
    supplier_code = serializers.CharField()
    supplier_name = serializers.CharField()
    currency = serializers.CharField()
    invoice_count = serializers.IntegerField()
    outstanding_balance = MoneySerializerField()
    overdue_amount = MoneySerializerField()
    oldest_due_date = serializers.DateField(allow_null=True)
    invoices = OutstandingInvoiceRowSerializer(many=True)


class OutstandingBalancesSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    total_outstanding = MoneySerializerField()
    supplier_count = serializers.IntegerField()
    invoice_count = serializers.IntegerField()
    ageing = AgeingBucketSerializer()
    suppliers = OutstandingSupplierRowSerializer(many=True)


class CashOutflowCategoryRowSerializer(serializers.Serializer):
    category_code = serializers.CharField()
    category_name = serializers.CharField()
    expense_count = serializers.IntegerField()
    total_amount = MoneySerializerField()


class CashOutflowSourceSerializer(serializers.Serializer):
    source = serializers.CharField()
    total_amount = MoneySerializerField()


class CashOutflowSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    total_outflow = MoneySerializerField()
    supplier_payments_total = MoneySerializerField()
    supplier_payment_count = serializers.IntegerField()
    expenses_total = MoneySerializerField()
    expense_count = serializers.IntegerField()
    unpaid_expenses_total = MoneySerializerField()
    expenses_by_category = CashOutflowCategoryRowSerializer(many=True)
    breakdown = CashOutflowSourceSerializer(many=True)


class FinancialOverviewSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    currency = serializers.CharField()

    sales_count = serializers.IntegerField()
    gross_revenue = MoneySerializerField()
    sales_tax = MoneySerializerField()
    net_revenue = MoneySerializerField()
    cost_of_goods = MoneySerializerField()
    gross_profit = MoneySerializerField()
    gross_margin_percent = serializers.DecimalField(max_digits=10, decimal_places=2)

    operating_expenses = MoneySerializerField()
    supplier_payments = MoneySerializerField()
    total_cash_outflow = MoneySerializerField()
    unpaid_expenses = MoneySerializerField()

    operating_result = MoneySerializerField()
    operating_margin_percent = serializers.DecimalField(max_digits=10, decimal_places=2)

    outstanding_payables = MoneySerializerField()
    supplier_count_owed = serializers.IntegerField()
