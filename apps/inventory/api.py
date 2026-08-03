from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import BusinessRuleViolation
from apps.core.pagination import StandardPagination
from apps.core.permissions import HasPermission

from . import services
from .models import BatchStatus, MovementType, StockBatch, StockMovement, Warehouse
from .serializers import (
    AdjustStockSerializer,
    BulkReceiveStockSerializer,
    ReceiveStockSerializer,
    ReconciliationSerializer,
    StockBatchSerializer,
    StockLevelSerializer,
    StockMovementSerializer,
    TransferStockSerializer,
    ValuationSerializer,
    WarehouseSerializer,
    WriteOffStockSerializer,
)

ZERO = Decimal("0")


class WarehouseViewSet(viewsets.ModelViewSet):
    queryset = Warehouse.objects.filter(deleted_at__isnull=True)
    serializer_class = WarehouseSerializer
    filterset_fields = ["is_active", "is_cold_chain", "city"]
    search_fields = ["code", "name_fr", "name_en", "city"]
    ordering = ["code"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "inventory.view_stock",
        "retrieve": "inventory.view_stock",
        "create": "inventory.manage_warehouse",
        "update": "inventory.manage_warehouse",
        "partial_update": "inventory.manage_warehouse",
        "destroy": "inventory.manage_warehouse",
    }


class StockBatchFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    expiring_within_days = filters.NumberFilter(method="filter_expiring")
    expired = filters.BooleanFilter(method="filter_expired")
    in_stock = filters.BooleanFilter(method="filter_in_stock")

    class Meta:
        model = StockBatch
        fields = ["product", "warehouse", "supplier", "status", "batch_number"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(batch_number__icontains=value)
            # Both language columns: a user searching in either language must
            # find the batch, whatever their interface locale.
            | Q(product__name_fr__icontains=value)
            | Q(product__name_en__icontains=value)
            | Q(product__product_code__icontains=value)
        )

    def filter_expiring(self, queryset, name, value):
        cutoff = timezone.localdate() + timezone.timedelta(days=int(value))
        return queryset.filter(expiry_date__lte=cutoff, expiry_date__gte=timezone.localdate())

    def filter_expired(self, queryset, name, value):
        today = timezone.localdate()
        return (
            queryset.filter(expiry_date__lt=today)
            if value
            else queryset.filter(expiry_date__gte=today)
        )

    def filter_in_stock(self, queryset, name, value):
        return (
            queryset.filter(quantity_remaining__gt=0)
            if value
            else queryset.filter(quantity_remaining__lte=0)
        )


@extend_schema_view(
    list=extend_schema(tags=["inventory"], summary="List stock batches"),
    retrieve=extend_schema(tags=["inventory"], summary="Retrieve a batch"),
)
class StockBatchViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Batches are read-mostly.

    There is deliberately no create or destroy route: a batch comes into
    existence only through a goods receipt, and it is never deleted — its
    history is the traceability record. Update is limited to notes and status
    by the serializer's read_only_fields.
    """

    queryset = StockBatch.objects.filter(deleted_at__isnull=True).select_related(
        "product", "warehouse", "supplier"
    )
    serializer_class = StockBatchSerializer
    filterset_class = StockBatchFilter
    search_fields = [
        "batch_number", "product__name_fr", "product__name_en", "product__product_code",
    ]
    ordering_fields = ["expiry_date", "received_at", "quantity_remaining"]
    ordering = ["expiry_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "inventory.view_batch",
        "retrieve": "inventory.view_batch",
        "update": "inventory.adjust_stock",
        "partial_update": "inventory.adjust_stock",
        "adjust": "inventory.adjust_stock",
        "transfer": "inventory.transfer_stock",
        "write_off": "inventory.dispose_stock",
        "movements": "inventory.view_stock",
        "reconcile": "inventory.view_stock",
    }

    @extend_schema(
        tags=["inventory"], summary="Adjust batch quantity (stocktake)",
        request=AdjustStockSerializer, responses={200: StockMovementSerializer},
    )
    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        """Correct a batch balance to a counted figure. Reason mandatory."""
        batch = self.get_object()
        serializer = AdjustStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        movement = services.adjust_stock(
            batch_id=batch.pk, performed_by=request.user, **serializer.validated_data
        )
        if movement is None:
            return Response({"detail": "No change: the counted quantity matches the system."})
        return Response(StockMovementSerializer(movement).data)

    @extend_schema(
        tags=["inventory"], summary="Transfer stock between warehouses",
        request=TransferStockSerializer,
    )
    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        batch = self.get_object()
        serializer = TransferStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        destination = get_object_or_404(Warehouse, pk=data.pop("destination"))
        out_move, in_move = services.transfer_stock(
            batch_id=batch.pk, destination=destination,
            performed_by=request.user, **data,
        )
        return Response(
            {
                "out": StockMovementSerializer(out_move).data,
                "in": StockMovementSerializer(in_move).data,
            }
        )

    @extend_schema(
        tags=["inventory"], summary="Write off stock (damage / expiry / disposal)",
        request=WriteOffStockSerializer, responses={200: StockMovementSerializer},
    )
    @action(detail=True, methods=["post"], url_path="write-off")
    def write_off(self, request, pk=None):
        """
        Remove stock permanently.

        Disposal of expired pharmaceuticals is a regulated act — this record
        is the evidence stock was destroyed rather than diverted.
        """
        batch = self.get_object()
        serializer = WriteOffStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        movement = services.write_off_stock(
            batch_id=batch.pk, performed_by=request.user, **serializer.validated_data
        )
        return Response(StockMovementSerializer(movement).data)

    @extend_schema(
        tags=["inventory"], summary="Movement history for this batch",
        responses={200: StockMovementSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def movements(self, request, pk=None):
        batch = self.get_object()
        page = self.paginate_queryset(
            batch.movements.select_related("performed_by").order_by("-performed_at")
        )
        return self.get_paginated_response(StockMovementSerializer(page, many=True).data)

    @extend_schema(
        tags=["inventory"], summary="Reconcile cached balance against the ledger",
        responses={200: ReconciliationSerializer},
    )
    @action(detail=True, methods=["get"])
    def reconcile(self, request, pk=None):
        batch = self.get_object()
        return Response(services.reconcile_batch(batch))


class StockMovementFilter(filters.FilterSet):
    date_from = filters.DateTimeFilter(field_name="performed_at", lookup_expr="gte")
    date_to = filters.DateTimeFilter(field_name="performed_at", lookup_expr="lte")

    class Meta:
        model = StockMovement
        fields = ["product", "batch", "warehouse", "movement_type", "performed_by", "source_type"]


@extend_schema_view(
    list=extend_schema(tags=["inventory"], summary="Stock ledger"),
    retrieve=extend_schema(tags=["inventory"], summary="Retrieve a movement"),
)
class StockMovementViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    The stock ledger. Read-only by construction.

    Corrections are made by posting a compensating movement through the
    relevant operation endpoint, never by editing history.
    """

    queryset = StockMovement.objects.select_related(
        "product", "batch", "warehouse", "performed_by"
    ).all()
    serializer_class = StockMovementSerializer
    filterset_class = StockMovementFilter
    search_fields = ["source_reference", "reason", "batch__batch_number"]
    ordering_fields = ["performed_at", "quantity_delta"]
    ordering = ["-performed_at"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "inventory.view_stock",
        "retrieve": "inventory.view_stock",
    }


class ReceiveStockView(APIView):
    """Direct stock receipt, outside the purchase-order flow."""

    permission_classes = [HasPermission]
    required_permissions = "inventory.receive_stock"

    @extend_schema(
        tags=["inventory"], summary="Receive stock directly",
        request=ReceiveStockSerializer, responses={201: StockBatchSerializer},
    )
    def post(self, request):
        from apps.catalog.models import Medicine
        from apps.partners.models import Supplier

        serializer = ReceiveStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        product = get_object_or_404(Medicine, pk=data.pop("product"))
        warehouse = get_object_or_404(Warehouse, pk=data.pop("warehouse"))
        supplier_id = data.pop("supplier", None)
        supplier = get_object_or_404(Supplier, pk=supplier_id) if supplier_id else None

        batch, _movement = services.receive_stock(
            product=product, warehouse=warehouse, supplier=supplier,
            performed_by=request.user, **data,
        )
        return Response(StockBatchSerializer(batch).data, status=201)


class BulkReceiveStockView(APIView):
    """
    Receive a whole delivery — many products, one transaction.

    This is the endpoint the receiving screen posts to. A clerk unpacking a
    shipment books every line together, so the write is all-or-nothing: a
    failure on any line rolls the whole delivery back rather than leaving a
    half-booked receipt for someone to reconcile by hand.
    """

    permission_classes = [HasPermission]
    required_permissions = "inventory.receive_stock"

    @extend_schema(
        tags=["inventory"], summary="Receive a multi-product delivery",
        request=BulkReceiveStockSerializer,
        responses={201: StockBatchSerializer(many=True)},
    )
    def post(self, request):
        from apps.catalog.models import Medicine
        from apps.partners.models import Supplier

        serializer = BulkReceiveStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        movement_type = (
            MovementType.OPENING
            if payload.get("is_opening_balance")
            else MovementType.RECEIPT
        )
        header_reference = payload.get("source_reference", "")
        header_notes = payload.get("notes", "")

        batches = []
        # One transaction across every line: see the serializer docstring.
        with transaction.atomic():
            for index, line in enumerate(payload["lines"]):
                data = dict(line)
                product = get_object_or_404(Medicine, pk=data.pop("product"))
                warehouse = get_object_or_404(Warehouse, pk=data.pop("warehouse"))
                supplier_id = data.pop("supplier", None)
                supplier = (
                    get_object_or_404(Supplier, pk=supplier_id) if supplier_id else None
                )

                # The delivery note applies to every line unless a line names
                # its own reference, so the clerk types it once.
                data["source_reference"] = data.get("source_reference") or header_reference
                data["notes"] = data.get("notes") or header_notes

                try:
                    batch, _movement = services.receive_stock(
                        product=product, warehouse=warehouse, supplier=supplier,
                        performed_by=request.user, movement_type=movement_type, **data,
                    )
                except BusinessRuleViolation as exc:
                    # Re-raised against the offending line so the form can put
                    # the message on the row that caused it. A delivery-wide
                    # error leaves the clerk hunting through twelve lines.
                    raise ValidationError({"lines": {index: [str(exc)]}}) from exc

                batches.append(batch)

        return Response(
            StockBatchSerializer(batches, many=True).data, status=201
        )


class StockLevelView(APIView):
    """
    Aggregated sellable stock per product.

    Paginated. The catalogue runs to thousands of products, and returning the
    lot in one response meant the browser rendered every row before showing
    any of them — the screen someone opens to check one product's availability
    was the slowest in the application. `page_size` still accepts up to the
    standard ceiling for a client that genuinely wants a long page.
    """

    permission_classes = [HasPermission]
    required_permissions = "inventory.view_stock"

    @extend_schema(
        tags=["inventory"], summary="Stock levels by product",
        parameters=[
            OpenApiParameter("warehouse", str, description="Warehouse UUID"),
            OpenApiParameter(
                "filter", str,
                description="One of: low_stock, out_of_stock, in_stock",
            ),
            OpenApiParameter("search", str, description="Product code or name"),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: StockLevelSerializer(many=True)},
    )
    def get(self, request):
        from apps.catalog.models import Medicine, ProductStatus

        today = timezone.localdate()
        warehouse_id = request.query_params.get("warehouse")
        mode = request.query_params.get("filter")
        search = (request.query_params.get("search") or "").strip()

        # Only ACTIVE, unexpired batches count as sellable — including expired
        # stock here would mask a genuine shortage.
        batches = StockBatch.objects.filter(
            status=BatchStatus.ACTIVE, expiry_date__gte=today, deleted_at__isnull=True,
        )
        if warehouse_id:
            batches = batches.filter(warehouse_id=warehouse_id)

        aggregates = {
            row["product_id"]: row
            for row in batches.values("product_id").annotate(
                on_hand=Sum("quantity_remaining"),
                available=Sum(F("quantity_remaining") - F("quantity_reserved")),
                batch_count=Count("id"),
            )
        }

        products = Medicine.objects.filter(
            status=ProductStatus.ACTIVE, deleted_at__isnull=True,
        # Both language columns: `name` is a property that resolves one of
        # them, so deferring either would trigger a per-row extra query.
        ).only("id", "product_code", "name_fr", "name_en", "reorder_level")

        if search:
            # Both names, because the operator may be reading either the
            # French or the English label off the shelf.
            products = products.filter(
                Q(product_code__icontains=search)
                | Q(name_fr__icontains=search)
                | Q(name_en__icontains=search)
            )

        # Ordered so pagination is stable: without it the database may return
        # rows in a different order per page and a product can appear twice or
        # not at all while the user is paging through.
        products = products.order_by("product_code")

        results = []
        for product in products.iterator(chunk_size=500):
            agg = aggregates.get(product.pk, {})
            on_hand = agg.get("on_hand") or ZERO
            available = agg.get("available") or ZERO
            is_out = available <= ZERO
            is_low = (
                not is_out
                and product.reorder_level > ZERO
                and available <= product.reorder_level
            )

            if mode == "low_stock" and not is_low:
                continue
            if mode == "out_of_stock" and not is_out:
                continue
            if mode == "in_stock" and is_out:
                continue

            results.append(
                {
                    "product_id": product.pk,
                    "product_code": product.product_code,
                    "product_name": product.name,
                    "reorder_level": product.reorder_level,
                    "quantity_on_hand": on_hand,
                    "quantity_available": available,
                    "batch_count": agg.get("batch_count", 0),
                    "is_low": is_low,
                    "is_out": is_out,
                }
            )

        # Paginated in Python rather than by the database: `is_low`/`is_out`
        # are derived from the batch aggregate, not stored, so the low- and
        # out-of-stock filters cannot be expressed as a queryset filter and the
        # page boundaries have to be applied after they run. The set is one row
        # per active product, which is small enough to build before slicing.
        paginator = StandardPagination()
        page = paginator.paginate_queryset(results, request, view=self)
        return paginator.get_paginated_response(page)


class ValuationView(APIView):
    permission_classes = [HasPermission]
    required_permissions = "inventory.view_valuation"

    @extend_schema(
        tags=["inventory"], summary="Inventory valuation at landed cost",
        responses={200: ValuationSerializer},
    )
    def get(self, request):
        warehouse_id = request.query_params.get("warehouse")
        warehouse = get_object_or_404(Warehouse, pk=warehouse_id) if warehouse_id else None
        return Response(services.inventory_valuation(warehouse=warehouse))


class ReconciliationView(APIView):
    """
    Batches whose cached balance disagrees with their ledger.

    Should always return empty. A non-empty result indicates a bug or
    out-of-band database modification — both are incidents.
    """

    permission_classes = [HasPermission]
    required_permissions = "inventory.view_stock"

    @extend_schema(
        tags=["inventory"], summary="Stock reconciliation report",
        responses={200: ReconciliationSerializer(many=True)},
    )
    def get(self, request):
        warehouse_id = request.query_params.get("warehouse")
        warehouse = get_object_or_404(Warehouse, pk=warehouse_id) if warehouse_id else None
        discrepancies = services.find_discrepancies(warehouse=warehouse)
        return Response(
            {"discrepancy_count": len(discrepancies), "discrepancies": discrepancies}
        )
