"""
Response shapes for report endpoints.

These exist mainly so the OpenAPI schema documents what each report returns —
without them the report views are omitted from the generated client, which
makes the frontend guess at field names.
"""

from rest_framework import serializers

from apps.core.fields import MoneySerializerField


class InventoryKPISerializer(serializers.Serializer):
    total_value = MoneySerializerField(required=False)
    total_products = serializers.IntegerField()
    total_batches = serializers.IntegerField()
    low_stock_products = serializers.IntegerField()
    out_of_stock_products = serializers.IntegerField()
    expiring_90_days = serializers.IntegerField()
    expired_batches = serializers.IntegerField()


class SalesKPISerializer(serializers.Serializer):
    daily_revenue = MoneySerializerField()
    daily_transactions = serializers.IntegerField()
    daily_margin = MoneySerializerField(required=False)
    monthly_revenue = MoneySerializerField()
    monthly_transactions = serializers.IntegerField()
    monthly_margin = MoneySerializerField(required=False)
    # The window the figures above actually cover. Present so a filtered
    # dashboard can label its tiles instead of still reading "today".
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    is_filtered = serializers.BooleanField()


class ReceivablesKPISerializer(serializers.Serializer):
    outstanding_total = MoneySerializerField()
    outstanding_count = serializers.IntegerField()
    overdue_total = MoneySerializerField()
    overdue_count = serializers.IntegerField()


class DashboardSerializer(serializers.Serializer):
    inventory = InventoryKPISerializer()
    sales = SalesKPISerializer()
    receivables = ReceivablesKPISerializer()
    currency = serializers.CharField()
    as_of = serializers.DateTimeField()


class RevenueTrendPointSerializer(serializers.Serializer):
    date = serializers.DateField()
    revenue = MoneySerializerField()
    cost = MoneySerializerField()
    margin = MoneySerializerField()
    transactions = serializers.IntegerField()


class TopCustomerSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField()
    customer_code = serializers.CharField()
    business_name = serializers.CharField()
    revenue = MoneySerializerField()
    transactions = serializers.IntegerField()


class TopProductSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    product_code = serializers.CharField()
    name = serializers.CharField()
    quantity_sold = serializers.DecimalField(max_digits=18, decimal_places=4)
    revenue = MoneySerializerField()
    margin = MoneySerializerField()


class DashboardWidgetsSerializer(serializers.Serializer):
    revenue_trend = RevenueTrendPointSerializer(many=True)
    top_customers = TopCustomerSerializer(many=True)
    top_products = TopProductSerializer(many=True)


class ValuationLineSerializer(serializers.Serializer):
    product_code = serializers.CharField()
    product_name = serializers.CharField()
    category = serializers.CharField()
    warehouse = serializers.CharField()
    batch_number = serializers.CharField()
    expiry_date = serializers.DateField()
    days_to_expiry = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3)
    unit_cost = MoneySerializerField()
    value = MoneySerializerField()


class ValuationReportSerializer(serializers.Serializer):
    lines = ValuationLineSerializer(many=True)
    total_value = MoneySerializerField()
    total_units = serializers.DecimalField(max_digits=18, decimal_places=4)
    batch_count = serializers.IntegerField()
    generated_at = serializers.DateTimeField()
    currency = serializers.CharField()


class ExpiryLineSerializer(serializers.Serializer):
    product_code = serializers.CharField()
    product_name = serializers.CharField()
    batch_number = serializers.CharField()
    warehouse = serializers.CharField()
    supplier = serializers.CharField()
    expiry_date = serializers.DateField()
    days_to_expiry = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3)
    unit_cost = MoneySerializerField()
    value_at_risk = MoneySerializerField()
    is_expired = serializers.BooleanField()


class ExpiryReportSerializer(serializers.Serializer):
    lines = ExpiryLineSerializer(many=True)
    horizon_days = serializers.IntegerField()
    total_value_at_risk = MoneySerializerField()
    already_expired_value = MoneySerializerField()
    batch_count = serializers.IntegerField()
    generated_at = serializers.DateTimeField()
    currency = serializers.CharField()


class MovementLineSerializer(serializers.Serializer):
    date = serializers.DateTimeField()
    product_code = serializers.CharField()
    product_name = serializers.CharField()
    batch_number = serializers.CharField()
    warehouse = serializers.CharField()
    movement_type = serializers.CharField()
    quantity_delta = serializers.DecimalField(max_digits=18, decimal_places=3)
    balance_after = serializers.DecimalField(max_digits=18, decimal_places=3)
    unit_cost = MoneySerializerField()
    value = MoneySerializerField()
    reference = serializers.CharField()
    performed_by = serializers.CharField()
    reason = serializers.CharField()


class MovementReportSerializer(serializers.Serializer):
    lines = MovementLineSerializer(many=True)
    movement_count = serializers.IntegerField()
    generated_at = serializers.DateTimeField()


class DeadStockLineSerializer(serializers.Serializer):
    product_code = serializers.CharField()
    product_name = serializers.CharField()
    batch_number = serializers.CharField()
    warehouse = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3)
    value = MoneySerializerField()
    expiry_date = serializers.DateField()
    received_at = serializers.DateTimeField()


class DeadStockReportSerializer(serializers.Serializer):
    lines = DeadStockLineSerializer(many=True)
    threshold_days = serializers.IntegerField()
    total_value = MoneySerializerField()
    generated_at = serializers.DateTimeField()
    currency = serializers.CharField()


class SalesReportLineSerializer(serializers.Serializer):
    period = serializers.DateField()
    revenue = MoneySerializerField()
    tax = MoneySerializerField()
    net_revenue = MoneySerializerField()
    cost = MoneySerializerField(required=False)
    margin = MoneySerializerField(required=False)
    margin_percent = serializers.DecimalField(
        max_digits=10, decimal_places=4, required=False,
    )
    discount = MoneySerializerField()
    transactions = serializers.IntegerField()


class SalesReportSerializer(serializers.Serializer):
    lines = SalesReportLineSerializer(many=True)
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    group_by = serializers.CharField()
    totals = serializers.DictField()
    generated_at = serializers.DateTimeField()
    currency = serializers.CharField()


class AgeingCustomerSerializer(serializers.Serializer):
    customer_code = serializers.CharField()
    business_name = serializers.CharField()
    credit_limit = MoneySerializerField()
    current = MoneySerializerField()
    total = MoneySerializerField()


class ReceivablesAgeingSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    buckets = serializers.DictField()
    total = MoneySerializerField()
    customers = AgeingCustomerSerializer(many=True)
    generated_at = serializers.DateTimeField()
    currency = serializers.CharField()


class ProfitLossSerializer(serializers.Serializer):
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    gross_revenue = MoneySerializerField()
    vat_collected = MoneySerializerField()
    net_revenue = MoneySerializerField()
    cost_of_goods_sold = MoneySerializerField()
    gross_profit = MoneySerializerField()
    gross_margin_percent = serializers.DecimalField(max_digits=10, decimal_places=4)
    discounts_granted = MoneySerializerField()
    note = serializers.CharField()
    generated_at = serializers.DateTimeField()
    currency = serializers.CharField()


class ComplianceLineSerializer(serializers.Serializer):
    date = serializers.DateTimeField()
    movement_type = serializers.CharField()
    product_code = serializers.CharField()
    product_name = serializers.CharField()
    batch_number = serializers.CharField()
    expiry_date = serializers.DateField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3)
    value = MoneySerializerField()
    reason = serializers.CharField()
    performed_by = serializers.CharField()
    reference = serializers.CharField()


class ComplianceReportSerializer(serializers.Serializer):
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    lines = ComplianceLineSerializer(many=True)
    movement_count = serializers.IntegerField()
    generated_at = serializers.DateTimeField()
