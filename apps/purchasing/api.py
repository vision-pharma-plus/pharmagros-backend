from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import BusinessRuleViolation
from apps.core.permissions import HasPermission

from . import services
from .models import (
    OPEN_SUPPLIER_INVOICE_STATUSES,
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierInvoice,
    SupplierPayment,
)
from .serializers import (
    AllocatePaymentSerializer,
    ApprovalSerializer,
    GoodsReceiptCreateSerializer,
    GoodsReceiptSerializer,
    PurchaseOrderCreateSerializer,
    PurchaseOrderListSerializer,
    PurchaseOrderSerializer,
    PurchaseOrderUpdateSerializer,
    RejectionSerializer,
    ReverseSerializer,
    SupplierBalanceSerializer,
    SupplierInvoiceCreateSerializer,
    SupplierInvoiceListSerializer,
    SupplierInvoiceReferenceSerializer,
    SupplierInvoiceSerializer,
    SupplierPaymentCreateSerializer,
    SupplierPaymentListSerializer,
    SupplierPaymentSerializer,
)


class PurchaseOrderFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="order_date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="order_date", lookup_expr="lte")
    search = filters.CharFilter(method="filter_search")
    awaiting_approval = filters.BooleanFilter(method="filter_awaiting")

    class Meta:
        model = PurchaseOrder
        fields = ["status", "supplier", "warehouse", "currency"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(order_number__icontains=value)
            | Q(supplier__name__icontains=value)
            | Q(supplier_reference__icontains=value)
            | Q(supplier_invoice_number__icontains=value)
        )

    def filter_awaiting(self, queryset, name, value):
        from .models import PurchaseOrderStatus

        return (
            queryset.filter(status=PurchaseOrderStatus.PENDING_APPROVAL)
            if value
            else queryset.exclude(status=PurchaseOrderStatus.PENDING_APPROVAL)
        )


@extend_schema_view(
    list=extend_schema(tags=["purchasing"], summary="List purchase orders"),
    retrieve=extend_schema(tags=["purchasing"], summary="Retrieve a purchase order"),
)
class PurchaseOrderViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = PurchaseOrder.objects.filter(deleted_at__isnull=True).select_related(
        "supplier", "warehouse", "requested_by", "approved_by"
    )
    filterset_class = PurchaseOrderFilter
    search_fields = ["order_number", "supplier__name"]
    ordering_fields = ["order_date", "total_amount", "expected_delivery_date"]
    ordering = ["-order_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "purchasing.view_order",
        "retrieve": "purchasing.view_order",
        "create": "purchasing.add_order",
        # Amending a draft is the same authority as raising one: the document
        # carries no approval yet, so there is nothing extra to protect.
        "update": "purchasing.add_order",
        "partial_update": "purchasing.add_order",
        "submit": "purchasing.submit_order",
        "approve": "purchasing.approve_order",
        "reject": "purchasing.approve_order",
        "mark_sent": "purchasing.approve_order",
        "cancel": "purchasing.cancel_order",
        "receive": "purchasing.receive_goods",
        "receipts": "purchasing.view_order",
        "supplier_invoice": "purchasing.record_supplier_invoice",
    }

    def get_serializer_class(self):
        if self.action == "list":
            return PurchaseOrderListSerializer
        if self.action == "create":
            return PurchaseOrderCreateSerializer
        if self.action in {"update", "partial_update"}:
            return PurchaseOrderUpdateSerializer
        return PurchaseOrderSerializer

    def get_queryset(self):
        return super().get_queryset().prefetch_related("lines__product")

    @staticmethod
    def _resolve_lines(raw_lines):
        """Swap product ids for instances, failing loudly on an unknown one."""
        from apps.catalog.models import Medicine

        product_ids = {line["product"] for line in raw_lines}
        products = {p.pk: p for p in Medicine.objects.filter(pk__in=product_ids)}

        missing = product_ids - set(products)
        if missing:
            raise BusinessRuleViolation(
                "One or more products on this order do not exist.",
                code="unknown_product",
                details={"product_ids": [str(m) for m in missing]},
            )

        return [{**line, "product": products[line["product"]]} for line in raw_lines]

    def create(self, request, *args, **kwargs):
        from apps.inventory.models import Warehouse
        from apps.partners.models import Supplier

        serializer = PurchaseOrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        supplier = get_object_or_404(Supplier, pk=data.pop("supplier"))
        warehouse = get_object_or_404(Warehouse, pk=data.pop("warehouse"))
        lines = self._resolve_lines(data.pop("lines"))

        order = services.create_order(
            supplier=supplier, warehouse=warehouse, lines=lines,
            actor=request.user, **data,
        )
        return Response(PurchaseOrderSerializer(order).data, status=201)

    def update(self, request, *args, **kwargs):
        """
        Amend a draft order.

        Lines are replaced wholesale rather than patched individually: an
        order is read and approved as one document, so "these are the lines
        now" is the only edit that has a meaningful audit trail. The service
        refuses anything past DRAFT/REJECTED.
        """
        from apps.inventory.models import Warehouse
        from apps.partners.models import Supplier

        order = self.get_object()
        partial = kwargs.pop("partial", False)
        serializer = PurchaseOrderUpdateSerializer(
            data=request.data, partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        supplier_id = data.pop("supplier", None)
        warehouse_id = data.pop("warehouse", None)
        raw_lines = data.pop("lines", None)

        order = services.update_order(
            order,
            supplier=(
                get_object_or_404(Supplier, pk=supplier_id) if supplier_id else None
            ),
            warehouse=(
                get_object_or_404(Warehouse, pk=warehouse_id) if warehouse_id else None
            ),
            lines=self._resolve_lines(raw_lines) if raw_lines is not None else None,
            actor=request.user,
            **data,
        )
        return Response(PurchaseOrderSerializer(order).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @extend_schema(tags=["purchasing"], summary="Submit for approval")
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        order = self.get_object()
        services.submit_for_approval(order, actor=request.user)
        return Response(PurchaseOrderSerializer(order).data)

    @extend_schema(
        tags=["purchasing"], summary="Approve a purchase order",
        request=ApprovalSerializer,
    )
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """
        Approve.

        Separation of duties is enforced in the service: the user who raised
        the order cannot approve it.
        """
        order = self.get_object()
        serializer = ApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.approve_order(
            order, actor=request.user, notes=serializer.validated_data.get("notes", "")
        )
        return Response(PurchaseOrderSerializer(order).data)

    @extend_schema(
        tags=["purchasing"], summary="Reject a purchase order",
        request=RejectionSerializer,
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        order = self.get_object()
        serializer = RejectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.reject_order(
            order, reason=serializer.validated_data["reason"], actor=request.user
        )
        return Response(PurchaseOrderSerializer(order).data)

    @extend_schema(tags=["purchasing"], summary="Mark as sent to supplier")
    @action(detail=True, methods=["post"], url_path="mark-sent")
    def mark_sent(self, request, pk=None):
        order = self.get_object()
        services.mark_sent(order, actor=request.user)
        return Response(PurchaseOrderSerializer(order).data)

    @extend_schema(
        tags=["purchasing"], summary="Cancel a purchase order",
        request=RejectionSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        serializer = RejectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.cancel_order(
            order, reason=serializer.validated_data["reason"], actor=request.user
        )
        return Response(PurchaseOrderSerializer(order).data)

    @extend_schema(
        tags=["purchasing"], summary="Receive goods against this order",
        request=GoodsReceiptCreateSerializer, responses={201: GoodsReceiptSerializer},
    )
    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """
        Book a delivery into stock.

        Creates real batches with landed costs including this delivery's share
        of freight and duty.
        """
        order = self.get_object()
        serializer = GoodsReceiptCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        raw_lines = data.pop("lines")
        line_ids = {line["purchase_order_line"] for line in raw_lines}
        po_lines = {
            line.pk: line
            for line in PurchaseOrderLine.objects.filter(pk__in=line_ids, purchase_order=order)
        }

        missing = line_ids - set(po_lines)
        if missing:
            raise BusinessRuleViolation(
                "One or more lines do not belong to this purchase order.",
                code="invalid_order_line",
                details={"line_ids": [str(m) for m in missing]},
            )

        lines = [
            {**line, "purchase_order_line": po_lines[line["purchase_order_line"]]}
            for line in raw_lines
        ]

        receipt = services.receive_goods(order, lines=lines, actor=request.user, **data)
        return Response(GoodsReceiptSerializer(receipt).data, status=201)

    @extend_schema(
        tags=["purchasing"], summary="Deliveries against this order",
        responses={200: GoodsReceiptSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def receipts(self, request, pk=None):
        order = self.get_object()
        return Response(GoodsReceiptSerializer(order.receipts.all(), many=True).data)

    @extend_schema(
        tags=["purchasing"], summary="Record the supplier's invoice reference",
        request=SupplierInvoiceReferenceSerializer,
    )
    @action(detail=True, methods=["post"], url_path="supplier-invoice")
    def supplier_invoice(self, request, pk=None):
        """
        Attach the supplier invoice reference for three-way matching.

        Records the number and date on the order only. To book what is *owed* —
        an amount, a due date, a balance that payments settle — create a
        SupplierInvoice through `/purchasing/supplier-invoices/`.
        """
        order = self.get_object()
        serializer = SupplierInvoiceReferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.record_supplier_invoice(
            order, actor=request.user, **serializer.validated_data
        )
        return Response(PurchaseOrderSerializer(order).data)


@extend_schema_view(
    list=extend_schema(tags=["purchasing"], summary="List goods receipts"),
    retrieve=extend_schema(tags=["purchasing"], summary="Retrieve a goods receipt"),
)
class GoodsReceiptViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Receipts are created through the order they belong to, never standalone."""

    queryset = GoodsReceipt.objects.filter(deleted_at__isnull=True).select_related(
        "purchase_order__supplier", "warehouse", "received_by"
    ).prefetch_related("lines__product")
    serializer_class = GoodsReceiptSerializer
    filterset_fields = ["purchase_order", "warehouse", "quality_checked"]
    search_fields = ["receipt_number", "delivery_note_number"]
    ordering = ["-receipt_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "purchasing.view_order",
        "retrieve": "purchasing.view_order",
    }


# ---------------------------------------------------------------------------
# Payables
# ---------------------------------------------------------------------------


class SupplierInvoiceFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="invoice_date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="invoice_date", lookup_expr="lte")
    due_before = filters.DateFilter(field_name="due_date", lookup_expr="lte")
    # "What still owes money" — the question the payments screen opens on.
    outstanding = filters.BooleanFilter(method="filter_outstanding")
    overdue = filters.BooleanFilter(method="filter_overdue")
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = SupplierInvoice
        fields = ["status", "supplier", "purchase_order", "currency"]

    def filter_outstanding(self, queryset, name, value):
        if value:
            return queryset.filter(status__in=OPEN_SUPPLIER_INVOICE_STATUSES)
        return queryset.exclude(status__in=OPEN_SUPPLIER_INVOICE_STATUSES)

    def filter_overdue(self, queryset, name, value):
        """
        Overdue is derived, not stored, so it is expressed as a filter here.

        A stored flag would need a nightly sweep to stay true and would be
        wrong for the hours between midnight and that job running.
        """
        today = timezone.localdate()
        if value:
            return queryset.filter(
                status__in=OPEN_SUPPLIER_INVOICE_STATUSES,
                due_date__lt=today,
                balance_due__gt=0,
            )
        return queryset.exclude(
            status__in=OPEN_SUPPLIER_INVOICE_STATUSES,
            due_date__lt=today,
            balance_due__gt=0,
        )

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(invoice_number__icontains=value)
            | Q(reference__icontains=value)
            | Q(supplier__name__icontains=value)
            | Q(purchase_order__order_number__icontains=value)
        )


@extend_schema_view(
    list=extend_schema(tags=["purchasing"], summary="List supplier invoices"),
    retrieve=extend_schema(tags=["purchasing"], summary="Retrieve a supplier invoice"),
    create=extend_schema(tags=["purchasing"], summary="Record a supplier invoice"),
)
class SupplierInvoiceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Supplier invoices — what the business owes and to whom.

    No update route: an invoice is the supplier's statement of what is owed,
    not our document to revise. A mistake is cancelled (which requires that no
    payment has been made against it) and re-entered, leaving both the error
    and the correction on file.
    """

    queryset = SupplierInvoice.objects.filter(deleted_at__isnull=True).select_related(
        "supplier", "purchase_order"
    )
    filterset_class = SupplierInvoiceFilter
    search_fields = ["invoice_number", "reference", "supplier__name"]
    ordering_fields = ["invoice_date", "due_date", "total_amount", "balance_due"]
    ordering = ["-invoice_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "purchasing.view_supplier_invoice",
        "retrieve": "purchasing.view_supplier_invoice",
        "create": "purchasing.record_supplier_invoice",
        "cancel": "purchasing.record_supplier_invoice",
        "payments": "purchasing.view_supplier_payment",
    }

    def get_serializer_class(self):
        if self.action == "list":
            return SupplierInvoiceListSerializer
        if self.action == "create":
            return SupplierInvoiceCreateSerializer
        return SupplierInvoiceSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related("payment_allocations__payment")
        return queryset

    def create(self, request, *args, **kwargs):
        from apps.partners.models import Supplier

        serializer = SupplierInvoiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        supplier = get_object_or_404(Supplier, pk=data.pop("supplier"))

        order_id = data.pop("purchase_order", None)
        purchase_order = (
            get_object_or_404(PurchaseOrder, pk=order_id) if order_id else None
        )

        invoice = services.create_supplier_invoice(
            supplier=supplier, purchase_order=purchase_order,
            actor=request.user, **data,
        )
        return Response(SupplierInvoiceSerializer(invoice).data, status=201)

    @extend_schema(
        tags=["purchasing"], summary="Cancel a supplier invoice",
        request=ReverseSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel an invoice recorded in error. Refused once it has payments."""
        invoice = self.get_object()
        serializer = ReverseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.cancel_supplier_invoice(
            invoice, reason=serializer.validated_data["reason"], actor=request.user
        )
        return Response(SupplierInvoiceSerializer(invoice).data)

    @extend_schema(
        tags=["purchasing"], summary="Payments against this invoice",
        responses={200: SupplierPaymentSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def payments(self, request, pk=None):
        """
        The settlement history for one invoice.

        The other half of "one invoice can have many payments": the invoice
        detail shows what it is owed and this shows every instalment against
        it, reversals included, since a reversed payment is part of the story
        of how the balance came to be what it is.
        """
        invoice = self.get_object()
        payments = (
            SupplierPayment.objects.filter(
                allocations__supplier_invoice=invoice, deleted_at__isnull=True
            )
            .select_related("supplier", "paid_by")
            .prefetch_related("allocations__supplier_invoice")
            .distinct()
            .order_by("-payment_date")
        )
        return Response(SupplierPaymentSerializer(payments, many=True).data)


class SupplierPaymentFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="payment_date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="payment_date", lookup_expr="lte")
    unallocated = filters.BooleanFilter(method="filter_unallocated")
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = SupplierPayment
        fields = ["supplier", "method", "is_reversed"]

    def filter_unallocated(self, queryset, name, value):
        """Payments still holding money on account, awaiting an invoice."""
        expression = Q(allocated_amount__lt=F("amount"), is_reversed=False)
        return queryset.filter(expression) if value else queryset.exclude(expression)

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(reference__icontains=value)
            | Q(payment_reference__icontains=value)
            | Q(bank_reference__icontains=value)
            | Q(supplier__name__icontains=value)
        )


@extend_schema_view(
    list=extend_schema(tags=["purchasing"], summary="List supplier payments"),
    retrieve=extend_schema(tags=["purchasing"], summary="Retrieve a supplier payment"),
    create=extend_schema(tags=["purchasing"], summary="Record a supplier payment"),
)
class SupplierPaymentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Payments made to suppliers.

    No update or destroy route: a payment records money that moved. It is
    corrected by reversal, which keeps both legs in the cash record so our
    books still reconcile against a bank statement.
    """

    queryset = SupplierPayment.objects.filter(deleted_at__isnull=True).select_related(
        "supplier", "paid_by"
    )
    filterset_class = SupplierPaymentFilter
    search_fields = ["reference", "payment_reference", "bank_reference", "supplier__name"]
    ordering_fields = ["payment_date", "amount", "reference"]
    ordering = ["-payment_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "purchasing.view_supplier_payment",
        "retrieve": "purchasing.view_supplier_payment",
        "create": "purchasing.record_supplier_payment",
        "allocate": "purchasing.record_supplier_payment",
        "reverse": "purchasing.reverse_supplier_payment",
        "balances": "purchasing.view_supplier_payment",
    }

    def get_serializer_class(self):
        if self.action == "list":
            return SupplierPaymentListSerializer
        if self.action == "create":
            return SupplierPaymentCreateSerializer
        return SupplierPaymentSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related("allocations__supplier_invoice")
        return queryset

    def create(self, request, *args, **kwargs):
        from apps.partners.models import Supplier

        serializer = SupplierPaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        supplier = get_object_or_404(Supplier, pk=data.pop("supplier"))

        payment = services.record_supplier_payment(
            supplier=supplier, actor=request.user, **data
        )
        return Response(SupplierPaymentSerializer(payment).data, status=201)

    @extend_schema(
        tags=["purchasing"], summary="Allocate a payment's remainder to invoices",
        request=AllocatePaymentSerializer,
    )
    @action(detail=True, methods=["post"])
    def allocate(self, request, pk=None):
        """Match money paid on account to invoices once they arrive."""
        payment = self.get_object()
        serializer = AllocatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.allocate_supplier_payment(
            payment,
            allocations=serializer.validated_data["allocations"],
            actor=request.user,
        )
        return Response(SupplierPaymentSerializer(payment).data)

    @extend_schema(
        tags=["purchasing"], summary="Reverse a supplier payment",
        request=ReverseSerializer,
    )
    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        """Reverse a bounced or mistaken payment, reopening the invoices it settled."""
        payment = self.get_object()
        serializer = ReverseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.reverse_supplier_payment(
            payment, reason=serializer.validated_data["reason"], actor=request.user
        )
        return Response(SupplierPaymentSerializer(payment).data)

    @extend_schema(
        tags=["purchasing"], summary="Outstanding balance per supplier",
        responses={200: SupplierBalanceSerializer(many=True)},
    )
    @action(detail=False, methods=["get"])
    def balances(self, request):
        """
        What is owed to every supplier, worst first.

        Ordered by outstanding amount descending so the largest exposure is at
        the top, which is the order the question is actually asked in.
        """
        return Response(services.supplier_balances())
