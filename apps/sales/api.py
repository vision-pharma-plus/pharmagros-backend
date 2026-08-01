from django.db.models import Q
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import BusinessRuleViolation
from apps.core.permissions import HasPermission

from . import services
from .models import Sale, SaleLine, SaleReturn
from .serializers import (
    CancelSerializer,
    ConfirmSaleSerializer,
    RecallTraceSerializer,
    SaleCreateSerializer,
    SaleListSerializer,
    SaleMarginSerializer,
    SaleReturnCreateSerializer,
    SaleReturnSerializer,
    SaleSerializer,
)


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
)
class SaleViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Sales.

    No update or destroy route: a draft is replaced by cancelling and
    re-creating, and a confirmed sale is a commercial record that can only be
    cancelled (with reason) or returned against.
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
        return SaleSerializer

    def create(self, request, *args, **kwargs):
        from apps.catalog.models import Medicine
        from apps.inventory.models import Warehouse
        from apps.partners.models import Customer

        serializer = SaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        customer = get_object_or_404(Customer, pk=data.pop("customer"))
        warehouse = get_object_or_404(Warehouse, pk=data.pop("warehouse"))

        # Resolve product UUIDs to instances before handing off to the service,
        # which works with model objects rather than raw identifiers.
        raw_lines = data.pop("lines")
        product_ids = {line["product"] for line in raw_lines}
        products = {p.pk: p for p in Medicine.objects.filter(pk__in=product_ids)}

        missing = product_ids - set(products)
        if missing:
            raise BusinessRuleViolation(
                "One or more products in this sale do not exist.",
                code="unknown_product",
                details={"product_ids": [str(m) for m in missing]},
            )

        lines = [{**line, "product": products[line["product"]]} for line in raw_lines]

        sale = services.create_sale(
            customer=customer, warehouse=warehouse, lines=lines,
            actor=request.user, **data,
        )
        return Response(SaleSerializer(sale).data, status=201)

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

        sale, invoice = services.confirm_sale(
            sale,
            actor=request.user,
            generate_invoice=data.get("generate_invoice", True),
            credit_override_reason=override_reason,
            # The authoriser is the acting user; recording it separately keeps
            # the audit trail explicit about who accepted the risk.
            credit_override_by=request.user if override_reason else None,
        )

        payload = SaleSerializer(sale).data
        payload["invoice_id"] = str(invoice.pk) if invoice else None
        payload["invoice_number"] = invoice.invoice_number if invoice else None
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
