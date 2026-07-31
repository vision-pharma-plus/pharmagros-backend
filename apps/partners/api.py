from django.db.models import Q
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import HasPermission

from . import services
from .models import Customer, CustomerContact, PartnerStatus, Supplier
from .serializers import (
    CreditBlockSerializer,
    CreditLimitChangeSerializer,
    CustomerContactSerializer,
    CustomerListSerializer,
    CustomerSerializer,
    CustomerStatementSerializer,
    SetCreditLimitSerializer,
    SupplierApprovalSerializer,
    SupplierPerformanceSerializer,
    SupplierSerializer,
)


class CustomerFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    has_outstanding = filters.BooleanFilter(method="filter_outstanding")
    over_limit = filters.BooleanFilter(method="filter_over_limit")

    class Meta:
        model = Customer
        fields = ["status", "customer_type", "city", "payment_terms", "credit_blocked"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(business_name__icontains=value)
            | Q(trading_name__icontains=value)
            | Q(customer_code__icontains=value)
            | Q(nif__icontains=value)
            | Q(contact_person__icontains=value)
        )

    def filter_outstanding(self, queryset, name, value):
        return (
            queryset.filter(outstanding_balance__gt=0)
            if value
            else queryset.filter(outstanding_balance__lte=0)
        )

    def filter_over_limit(self, queryset, name, value):
        from django.db.models import F

        qs = queryset.filter(credit_limit__gt=0)
        return (
            qs.filter(outstanding_balance__gt=F("credit_limit"))
            if value
            else qs.filter(outstanding_balance__lte=F("credit_limit"))
        )


@extend_schema_view(
    list=extend_schema(tags=["partners"], summary="List customers"),
    retrieve=extend_schema(tags=["partners"], summary="Retrieve a customer"),
    create=extend_schema(tags=["partners"], summary="Create a customer"),
    update=extend_schema(tags=["partners"], summary="Update a customer"),
)
class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.filter(deleted_at__isnull=True)
    filterset_class = CustomerFilter
    search_fields = ["business_name", "customer_code", "nif", "contact_person"]
    ordering_fields = ["business_name", "customer_code", "outstanding_balance", "created_at"]
    ordering = ["business_name"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "partners.view_customer",
        "retrieve": "partners.view_customer",
        "create": "partners.add_customer",
        "update": "partners.change_customer",
        "partial_update": "partners.change_customer",
        "destroy": "partners.delete_customer",
        "set_credit_limit": "partners.set_credit_limit",
        "block_credit": "partners.set_credit_limit",
        "unblock_credit": "partners.set_credit_limit",
        "statement": "partners.view_statement",
        "credit_history": "partners.view_statement",
        "transactions": "partners.view_customer",
        "recompute_balance": "partners.change_customer",
    }

    def get_serializer_class(self):
        return CustomerListSerializer if self.action == "list" else CustomerSerializer

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        data.pop("credit_limit_reason", None)
        serializer.instance = services.create_customer(actor=self.request.user, **data)

    def perform_update(self, serializer):
        serializer.instance = services.update_customer(
            serializer.instance, actor=self.request.user, **serializer.validated_data
        )

    def perform_destroy(self, instance):
        """Soft delete — historical invoices reference this customer."""
        instance.status = PartnerStatus.INACTIVE
        instance.save(update_fields=["status", "updated_at"])
        instance.delete(actor=self.request.user)

    @extend_schema(
        tags=["partners"], summary="Set credit limit", request=SetCreditLimitSerializer,
        responses={200: CreditLimitChangeSerializer},
    )
    @action(detail=True, methods=["post"], url_path="credit-limit")
    def set_credit_limit(self, request, pk=None):
        customer = self.get_object()
        serializer = SetCreditLimitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        change = services.set_credit_limit(
            customer,
            serializer.validated_data["credit_limit"],
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return Response(CreditLimitChangeSerializer(change).data)

    @extend_schema(tags=["partners"], summary="Block credit", request=CreditBlockSerializer)
    @action(detail=True, methods=["post"], url_path="block-credit")
    def block_credit(self, request, pk=None):
        customer = self.get_object()
        serializer = CreditBlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.block_credit(
            customer, reason=serializer.validated_data["reason"], actor=request.user
        )
        return Response(CustomerSerializer(customer).data)

    @extend_schema(tags=["partners"], summary="Unblock credit")
    @action(detail=True, methods=["post"], url_path="unblock-credit")
    def unblock_credit(self, request, pk=None):
        customer = self.get_object()
        services.unblock_credit(
            customer, reason=request.data.get("reason", ""), actor=request.user
        )
        return Response(CustomerSerializer(customer).data)

    @extend_schema(
        tags=["partners"], summary="Account statement",
        responses={200: CustomerStatementSerializer},
    )
    @action(detail=True, methods=["get"])
    def statement(self, request, pk=None):
        """Chronological invoice/payment ledger with a running balance."""
        customer = self.get_object()
        statement = services.customer_statement(
            customer,
            date_from=request.query_params.get("date_from"),
            date_to=request.query_params.get("date_to"),
        )
        return Response(
            {
                "customer_code": customer.customer_code,
                "business_name": customer.business_name,
                "date_from": statement["date_from"],
                "date_to": statement["date_to"],
                "lines": statement["lines"],
                "total_invoiced": statement["total_invoiced"],
                "total_paid": statement["total_paid"],
                "closing_balance": statement["closing_balance"],
                "currency": statement["currency"],
            }
        )

    @extend_schema(
        tags=["partners"], summary="Credit limit history",
        responses={200: CreditLimitChangeSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="credit-history")
    def credit_history(self, request, pk=None):
        customer = self.get_object()
        return Response(
            CreditLimitChangeSerializer(customer.credit_limit_history.all(), many=True).data
        )

    @extend_schema(tags=["partners"], summary="Transaction history")
    @action(detail=True, methods=["get"])
    def transactions(self, request, pk=None):
        customer = self.get_object()
        sales = (
            customer.sales.exclude(status="DRAFT")
            .select_related("warehouse")
            .order_by("-sale_date")[:100]
        )
        return Response(
            [
                {
                    "sale_number": s.sale_number,
                    "date": s.sale_date,
                    "type": s.sale_type,
                    "status": s.status,
                    "total_amount": str(s.total_amount),
                    "invoice_number": getattr(getattr(s, "invoice", None), "invoice_number", None),
                }
                for s in sales
            ]
        )

    @extend_schema(tags=["partners"], summary="Recompute outstanding balance")
    @action(detail=True, methods=["post"], url_path="recompute-balance")
    def recompute_balance(self, request, pk=None):
        """
        Force a balance recalculation from posted invoices.

        Exposed for reconciliation: if a balance is ever disputed, this proves
        the figure is derived from documents rather than a drifting counter.
        """
        customer = self.get_object()
        balance = services.recompute_balance(customer)
        return Response({"outstanding_balance": str(balance)})


class CustomerContactViewSet(viewsets.ModelViewSet):
    queryset = CustomerContact.objects.filter(deleted_at__isnull=True).select_related("customer")
    serializer_class = CustomerContactSerializer
    filterset_fields = ["customer", "is_primary"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "partners.view_customer",
        "retrieve": "partners.view_customer",
        "create": "partners.change_customer",
        "update": "partners.change_customer",
        "partial_update": "partners.change_customer",
        "destroy": "partners.change_customer",
    }


class SupplierFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = Supplier
        fields = ["status", "country", "is_approved", "currency"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value)
            | Q(supplier_code__icontains=value)
            | Q(nif__icontains=value)
            | Q(contact_person__icontains=value)
        )


@extend_schema_view(
    list=extend_schema(tags=["partners"], summary="List suppliers"),
    retrieve=extend_schema(tags=["partners"], summary="Retrieve a supplier"),
)
class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.filter(deleted_at__isnull=True)
    serializer_class = SupplierSerializer
    filterset_class = SupplierFilter
    search_fields = ["name", "supplier_code", "nif"]
    ordering_fields = ["name", "supplier_code", "created_at"]
    ordering = ["name"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "partners.view_supplier",
        "retrieve": "partners.view_supplier",
        "create": "partners.add_supplier",
        "update": "partners.change_supplier",
        "partial_update": "partners.change_supplier",
        "destroy": "partners.delete_supplier",
        "approve": "partners.change_supplier",
        "performance": "partners.view_supplier",
        "purchase_history": "purchasing.view_order",
    }

    def perform_create(self, serializer):
        serializer.instance = services.create_supplier(
            actor=self.request.user, **serializer.validated_data
        )

    def perform_destroy(self, instance):
        instance.status = PartnerStatus.INACTIVE
        instance.save(update_fields=["status", "updated_at"])
        instance.delete(actor=self.request.user)

    @extend_schema(
        tags=["partners"], summary="Approve supplier for procurement",
        request=SupplierApprovalSerializer,
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Only approved suppliers may be selected on a purchase order."""
        supplier = self.get_object()
        serializer = SupplierApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.approve_supplier(
            supplier, notes=serializer.validated_data.get("notes", ""), actor=request.user
        )
        return Response(SupplierSerializer(supplier).data)

    @extend_schema(
        tags=["partners"], summary="Delivery performance metrics",
        responses={200: SupplierPerformanceSerializer},
    )
    @action(detail=True, methods=["get"])
    def performance(self, request, pk=None):
        supplier = self.get_object()
        metrics = services.supplier_performance(
            supplier,
            date_from=request.query_params.get("date_from"),
            date_to=request.query_params.get("date_to"),
        )
        metrics.pop("supplier", None)
        return Response(metrics)

    @extend_schema(tags=["partners"], summary="Purchase order history")
    @action(detail=True, methods=["get"], url_path="purchase-history")
    def purchase_history(self, request, pk=None):
        supplier = self.get_object()
        orders = supplier.purchase_orders.filter(deleted_at__isnull=True).order_by("-order_date")[:100]
        return Response(
            [
                {
                    "order_number": o.order_number,
                    "order_date": o.order_date,
                    "status": o.status,
                    "total_amount": str(o.total_amount),
                    "expected_delivery_date": o.expected_delivery_date,
                    "actual_delivery_date": o.actual_delivery_date,
                }
                for o in orders
            ]
        )
