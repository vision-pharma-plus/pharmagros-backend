import logging

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import BusinessRuleViolation, ServiceUnavailable
from apps.core.permissions import HasPermission

from . import services
from .models import Sale, SaleLine, SaleReturn, SalesReceipt
from .serializers import (
    CancelSerializer,
    ConfirmSaleSerializer,
    DeleteDraftSerializer,
    InvoiceForReceiptSerializer,
    RecallTraceSerializer,
    SaleCreateSerializer,
    SaleListSerializer,
    SaleMarginSerializer,
    SaleReturnCreateSerializer,
    SaleReturnSerializer,
    SaleSerializer,
    SalesReceiptListSerializer,
    SalesReceiptSerializer,
    SaleUpdateSerializer,
)

logger = logging.getLogger(__name__)


class SaleFilter(filters.FilterSet):
    date_from = filters.DateTimeFilter(field_name="sale_date", lookup_expr="gte")
    date_to = filters.DateTimeFilter(field_name="sale_date", lookup_expr="lte")
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = Sale
        fields = ["status", "sale_type", "customer", "warehouse", "salesperson"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(sale_number__icontains=value)
            | Q(customer__business_name__icontains=value)
            | Q(customer_order_reference__icontains=value)
        )


@extend_schema_view(
    list=extend_schema(tags=["sales"], summary="List sales"),
    retrieve=extend_schema(tags=["sales"], summary="Retrieve a sale"),
    update=extend_schema(tags=["sales"], summary="Replace a draft sale"),
    partial_update=extend_schema(tags=["sales"], summary="Amend a draft sale"),
    destroy=extend_schema(tags=["sales"], summary="Delete a draft sale"),
)
class SaleViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Sales.

    Update and destroy apply to *drafts only*, and the restriction lives in the
    service layer (`update_sale`, `delete_draft_sale`) rather than here, so it
    holds for every caller. A confirmed sale has moved stock and put a document
    in the customer's hands: it is corrected by cancellation or a return, never
    by editing what it says or removing the row.
    """

    queryset = Sale.objects.filter(deleted_at__isnull=True).select_related(
        "customer", "warehouse", "salesperson"
    )
    filterset_class = SaleFilter
    search_fields = ["sale_number", "customer__business_name"]
    ordering_fields = ["sale_date", "total_amount", "sale_number"]
    ordering = ["-sale_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "sales.view_sale",
        "retrieve": "sales.view_sale",
        "create": "sales.add_sale",
        "update": "sales.change_sale",
        "partial_update": "sales.change_sale",
        # Deleting a draft is destructive in a way editing is not, so it sits
        # with the cancellation authority rather than with ordinary editing.
        "destroy": "sales.cancel_sale",
        "confirm": "sales.add_sale",
        "cancel": "sales.cancel_sale",
        "process_return": "sales.process_return",
        "margin": "sales.view_margin",
        "returns": "sales.view_sale",
    }

    def get_serializer_class(self):
        if self.action == "list":
            return SaleListSerializer
        if self.action == "create":
            return SaleCreateSerializer
        if self.action in {"update", "partial_update"}:
            return SaleUpdateSerializer
        return SaleSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            # The detail view renders every line with its product and batches.
            queryset = queryset.prefetch_related(
                "lines__product", "lines__batch_allocations"
            )
        return queryset

    def _resolve_lines(self, raw_lines):
        """
        Turn line payloads carrying product UUIDs into ones carrying instances.

        The services work with model objects, not identifiers. Unknown products
        are reported together rather than one per round trip, so an operator
        fixing a bad import sees the whole list at once.
        """
        from apps.catalog.models import Medicine

        product_ids = {line["product"] for line in raw_lines}
        products = {p.pk: p for p in Medicine.objects.filter(pk__in=product_ids)}

        missing = product_ids - set(products)
        if missing:
            raise BusinessRuleViolation(
                "One or more products in this sale do not exist.",
                code="unknown_product",
                details={"product_ids": [str(m) for m in missing]},
            )

        return [{**line, "product": products[line["product"]]} for line in raw_lines]

    def create(self, request, *args, **kwargs):
        from apps.inventory.models import Warehouse
        from apps.partners.models import Customer

        serializer = SaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        customer = get_object_or_404(Customer, pk=data.pop("customer"))
        warehouse = get_object_or_404(Warehouse, pk=data.pop("warehouse"))
        lines = self._resolve_lines(data.pop("lines"))

        sale = services.create_sale(
            customer=customer, warehouse=warehouse, lines=lines,
            actor=request.user, **data,
        )
        return Response(SaleSerializer(sale).data, status=201)

    def update(self, request, *args, **kwargs):
        """
        Amend a draft sale.

        PATCH and PUT behave identically: both accept a partial payload and
        leave unsent fields alone. A sale is a composite document rather than a
        flat resource — a PUT that blanked every omitted header field would
        wipe the delivery reference and notes on any client that sent only the
        lines it had changed.
        """
        from apps.inventory.models import Warehouse
        from apps.partners.models import Customer

        sale = self.get_object()
        serializer = SaleUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        if "customer" in data:
            data["customer"] = get_object_or_404(Customer, pk=data["customer"])
        if "warehouse" in data:
            data["warehouse"] = get_object_or_404(Warehouse, pk=data["warehouse"])
        if "lines" in data:
            data["lines"] = self._resolve_lines(data["lines"])

        sale = services.update_sale(sale, actor=request.user, **data)
        return Response(SaleSerializer(sale).data)

    def destroy(self, request, *args, **kwargs):
        """
        Delete a draft sale.

        Soft-deleted by the service, so the number remains accounted for. The
        reason may be supplied in the body and is recorded in the audit entry.
        """
        sale = self.get_object()
        serializer = DeleteDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.delete_draft_sale(
            sale, reason=serializer.validated_data.get("reason", ""), actor=request.user
        )
        return Response(status=204)

    @extend_schema(
        tags=["sales"], summary="Confirm a sale (issues stock and invoices)",
        request=ConfirmSaleSerializer,
    )
    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """
        Confirm: credit check, FIFO stock issue, traceability, invoice.

        All in one transaction — either the whole commercial event happens or
        none of it does.
        """
        sale = self.get_object()
        serializer = ConfirmSaleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        override_reason = data.get("credit_override_reason", "")
        if override_reason and not request.user.has_perm_code("sales.override_credit_limit"):
            raise BusinessRuleViolation(
                "You do not have permission to override a customer's credit limit.",
                code="override_not_permitted",
            )

        sale, invoice, receipt = services.confirm_sale(
            sale,
            actor=request.user,
            generate_invoice=data.get("generate_invoice"),
            payment_method=data.get("payment_method", "CASH"),
            payment_reference=data.get("payment_reference", ""),
            amount_tendered=data.get("amount_tendered"),
            credit_override_reason=override_reason,
            # The authoriser is the acting user; recording it separately keeps
            # the audit trail explicit about who accepted the risk.
            credit_override_by=request.user if override_reason else None,
        )

        payload = SaleSerializer(sale).data
        payload["invoice_id"] = str(invoice.pk) if invoice else None
        payload["invoice_number"] = invoice.invoice_number if invoice else None
        # The document the operator should be taken to. For a cash sale that is
        # the receipt, which is the only thing the customer is waiting for.
        payload["receipt_id"] = str(receipt.pk) if receipt else None
        payload["receipt_number"] = receipt.receipt_number if receipt else None
        return Response(payload)

    @extend_schema(tags=["sales"], summary="Cancel a sale", request=CancelSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel and return any issued stock to its original batches."""
        sale = self.get_object()
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.cancel_sale(
            sale, reason=serializer.validated_data["reason"], actor=request.user
        )
        return Response(SaleSerializer(sale).data)

    @extend_schema(
        tags=["sales"], summary="Process a customer return",
        request=SaleReturnCreateSerializer, responses={201: SaleReturnSerializer},
    )
    @action(detail=True, methods=["post"], url_path="returns")
    def process_return(self, request, pk=None):
        from apps.inventory.models import StockBatch

        sale = self.get_object()
        serializer = SaleReturnCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        raw_lines = data.pop("lines")
        line_ids = {line["sale_line"] for line in raw_lines}
        sale_lines = {sl.pk: sl for sl in SaleLine.objects.filter(pk__in=line_ids, sale=sale)}

        missing = line_ids - set(sale_lines)
        if missing:
            raise BusinessRuleViolation(
                "One or more lines do not belong to this sale.",
                code="invalid_sale_line",
                details={"sale_line_ids": [str(m) for m in missing]},
            )

        batch_ids = {line["batch"] for line in raw_lines if line.get("batch")}
        batches = {b.pk: b for b in StockBatch.objects.filter(pk__in=batch_ids)}

        lines = [
            {
                **line,
                "sale_line": sale_lines[line["sale_line"]],
                "batch": batches.get(line["batch"]) if line.get("batch") else None,
            }
            for line in raw_lines
        ]

        sale_return = services.process_return(
            sale, lines=lines, actor=request.user, **data
        )
        return Response(SaleReturnSerializer(sale_return).data, status=201)

    @extend_schema(
        tags=["sales"], summary="Returns against this sale",
        responses={200: SaleReturnSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="return-history")
    def returns(self, request, pk=None):
        sale = self.get_object()
        return Response(SaleReturnSerializer(sale.returns.all(), many=True).data)

    @extend_schema(
        tags=["sales"], summary="Margin on this sale",
        responses={200: SaleMarginSerializer},
    )
    @action(detail=True, methods=["get"])
    def margin(self, request, pk=None):
        """Margin computed from the actual batch costs issued, not catalogue cost."""
        sale = self.get_object()
        return Response(
            {
                "sale_number": sale.sale_number,
                "revenue_net_of_tax": sale.total_amount - sale.tax_amount,
                "cost_of_goods": sale.total_cost,
                "gross_margin": sale.gross_margin,
                "gross_margin_percent": sale.gross_margin_percent,
                "currency": "BIF",
            }
        )


@extend_schema_view(
    list=extend_schema(tags=["sales"], summary="List returns"),
    retrieve=extend_schema(tags=["sales"], summary="Retrieve a return"),
)
class SaleReturnViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Returns are created through the sale they relate to, never standalone."""

    queryset = SaleReturn.objects.filter(deleted_at__isnull=True).select_related(
        "sale", "customer", "credit_note", "processed_by"
    )
    serializer_class = SaleReturnSerializer
    filterset_fields = ["sale", "customer"]
    search_fields = ["return_number", "sale__sale_number", "reason"]
    ordering = ["-return_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "sales.view_sale",
        "retrieve": "sales.view_sale",
    }


class SalesReceiptFilter(filters.FilterSet):
    date_from = filters.DateTimeFilter(field_name="issued_at", lookup_expr="gte")
    date_to = filters.DateTimeFilter(field_name="issued_at", lookup_expr="lte")
    # Answers "which counter sales still have no invoice", which is what the
    # end-of-day check and any tax-driven invoicing sweep both need.
    invoiced = filters.BooleanFilter(method="filter_invoiced")
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = SalesReceipt
        fields = ["customer", "payment_method"]

    def filter_invoiced(self, queryset, name, value):
        return queryset.filter(sale__invoice__isnull=not value)

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(receipt_number__icontains=value)
            | Q(customer_name__icontains=value)
            | Q(sale__sale_number__icontains=value)
        )


@extend_schema_view(
    list=extend_schema(tags=["sales"], summary="List sales receipts"),
    retrieve=extend_schema(tags=["sales"], summary="Retrieve a sales receipt"),
)
class SalesReceiptViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """
    Cash sale receipts.

    Created only by confirming a cash sale — never directly, because a receipt
    without the sale behind it would be proof of a payment for nothing. There
    is likewise no update or destroy route: a receipt is cancelled by
    cancelling its sale, which is what also returns the stock.
    """

    queryset = SalesReceipt.objects.filter(deleted_at__isnull=True).select_related(
        "sale", "customer", "issued_by", "sale__invoice"
    )
    filterset_class = SalesReceiptFilter
    search_fields = ["receipt_number", "customer_name", "sale__sale_number"]
    ordering_fields = ["issued_at", "receipt_number"]
    ordering = ["-issued_at"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "sales.view_sale",
        "retrieve": "sales.view_sale",
        "pdf": "sales.print_receipt",
        "invoice": "sales.invoice_receipt",
    }

    def get_serializer_class(self):
        if self.action == "list":
            return SalesReceiptListSerializer
        return SalesReceiptSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != "list":
            # The detail view renders the sale's lines and their batches.
            queryset = queryset.prefetch_related(
                "sale__lines__product", "sale__lines__batch_allocations"
            )
        return queryset

    @extend_schema(
        tags=["sales"],
        summary="Issue an invoice for this cash sale",
        request=InvoiceForReceiptSerializer,
        responses={201: None},
    )
    @action(detail=True, methods=["post"])
    def invoice(self, request, pk=None):
        """
        Raise an invoice for an already-receipted cash sale.

        Covers both the customer who asks for an invoice after paying and the
        tax rule that requires one. The receipt stays valid and the sale is not
        repeated: no stock moves, no tax is recomputed, no revenue is posted a
        second time. See `services.invoice_for_receipt`.
        """
        from apps.invoicing.serializers import InvoiceSerializer

        receipt = self.get_object()
        serializer = InvoiceForReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        invoice = services.invoice_for_receipt(
            receipt,
            reason=serializer.validated_data.get("reason", ""),
            actor=request.user,
        )
        return Response(InvoiceSerializer(invoice).data, status=201)

    @extend_schema(
        tags=["sales"],
        summary="Download receipt PDF",
        parameters=[
            OpenApiParameter(
                "language", str, description="fr or en. Defaults to fr.",
            )
        ],
        responses={200: bytes},
    )
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """
        Render the receipt as an 80 mm counter document.

        Registered before rendering so a printed receipt is never served
        without a matching audit entry, and released again if the engine is
        missing — the same contract as the invoice PDF route.
        """
        receipt = self.get_object()
        language = request.query_params.get("language") or "fr"

        from .pdf import PDFEngineUnavailable, render_receipt_pdf

        copy_number = services.register_receipt_print(receipt, actor=request.user)

        try:
            pdf_bytes = render_receipt_pdf(receipt, language=language)
        except PDFEngineUnavailable as exc:
            # A deployment fault, not a bad request: hand back a 503 with
            # setup instructions and give the copy number back, so the reprint
            # history does not show copies that were never produced.
            services.release_receipt_print(
                receipt, copy_number, reason="PDF engine unavailable", actor=request.user
            )
            logger.exception("Receipt PDF rendering is unavailable")
            raise ServiceUnavailable(str(exc)) from exc

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{receipt.receipt_number}.pdf"'
        )
        return response


class RecallTraceView(APIView):
    """
    Recall query: every customer who received units from a batch.

    The single most important endpoint in a pharmaceutical recall — it turns a
    batch number into a contact list within seconds.
    """

    permission_classes = [HasPermission]
    required_permissions = "sales.view_sale"

    @extend_schema(
        tags=["sales"], summary="Trace batch recipients (recall)",
        parameters=[
            OpenApiParameter(
                "batch_number", str, required=True,
                description="Batch number to trace",
            )
        ],
        responses={200: RecallTraceSerializer(many=True)},
    )
    def get(self, request):
        batch_number = request.query_params.get("batch_number")
        if not batch_number:
            raise BusinessRuleViolation(
                "A batch number is required to run a recall trace.",
                code="batch_number_required",
            )

        recipients = services.trace_batch_recipients(batch_number)
        total = sum(r["quantity_outstanding"] for r in recipients)
        return Response(
            {
                "batch_number": batch_number,
                "recipient_count": len(recipients),
                "total_quantity_outstanding": str(total),
                "recipients": recipients,
            }
        )
