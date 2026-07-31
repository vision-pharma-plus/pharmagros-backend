from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import HasPermission

from . import services
from .models import Category, Manufacturer, Medicine, ProductStatus, UnitOfMeasure
from .serializers import (
    CategorySerializer,
    ManufacturerSerializer,
    MedicineDetailSerializer,
    MedicineListSerializer,
    PriceChangeSerializer,
    PriceHistorySerializer,
    UnitOfMeasureSerializer,
)


class MedicineFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")
    category = filters.UUIDFilter(field_name="category__id")
    min_price = filters.NumberFilter(field_name="selling_price", lookup_expr="gte")
    max_price = filters.NumberFilter(field_name="selling_price", lookup_expr="lte")
    requires_cold_chain = filters.BooleanFilter(method="filter_cold_chain")

    class Meta:
        model = Medicine
        fields = [
            "status", "category", "manufacturer", "dosage_form",
            "requires_prescription", "is_controlled", "is_vat_exempt",
        ]

    def filter_search(self, queryset, name, value):
        """
        Search across the identifiers staff actually type.

        Barcode is included because warehouse users scan rather than type, and
        generic name because a pharmacist searching for 'paracetamol' must
        find products branded 'Doliprane'.
        """
        from django.db.models import Q

        return queryset.filter(
            Q(name_fr__icontains=value)
            | Q(name_en__icontains=value)
            | Q(generic_name__icontains=value)
            | Q(brand_name_fr__icontains=value)
            | Q(brand_name_en__icontains=value)
            | Q(product_code__icontains=value)
            | Q(barcode=value)
        )

    def filter_cold_chain(self, queryset, name, value):
        from .models import StorageCondition

        cold = [StorageCondition.REFRIGERATED, StorageCondition.FROZEN]
        return (
            queryset.filter(storage_condition__in=cold)
            if value
            else queryset.exclude(storage_condition__in=cold)
        )


@extend_schema_view(
    list=extend_schema(tags=["catalog"], summary="List medicines"),
    retrieve=extend_schema(tags=["catalog"], summary="Retrieve a medicine"),
    create=extend_schema(tags=["catalog"], summary="Create a medicine"),
    update=extend_schema(tags=["catalog"], summary="Update a medicine"),
    destroy=extend_schema(tags=["catalog"], summary="Archive a medicine"),
)
class MedicineViewSet(viewsets.ModelViewSet):
    queryset = (
        Medicine.objects.select_related("category", "manufacturer", "unit_of_measure")
        .filter(deleted_at__isnull=True)
    )
    filterset_class = MedicineFilter
    search_fields = ["name_fr", "name_en", "generic_name", "product_code", "barcode"]
    ordering_fields = ["name_fr", "name_en", "product_code", "selling_price", "created_at"]
    ordering = ["name_fr"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "catalog.view_medicine",
        "retrieve": "catalog.view_medicine",
        "create": "catalog.add_medicine",
        "update": "catalog.change_medicine",
        "partial_update": "catalog.change_medicine",
        "destroy": "catalog.delete_medicine",
        "change_price": "catalog.change_price",
        "price_history": "catalog.view_medicine",
        "stock": "inventory.view_stock",
    }

    def get_serializer_class(self):
        return (
            MedicineListSerializer if self.action == "list" else MedicineDetailSerializer
        )

    def perform_create(self, serializer):
        serializer.instance = services.create_medicine(
            actor=self.request.user, **serializer.validated_data
        )

    def perform_update(self, serializer):
        serializer.instance = services.update_medicine(
            serializer.instance, actor=self.request.user, **serializer.validated_data
        )

    def perform_destroy(self, instance):
        # Soft delete: a product referenced by historical invoices must never
        # physically disappear, or those documents become unreadable.
        services.set_status(
            instance, ProductStatus.DISCONTINUED,
            reason="Archived via API", actor=self.request.user,
        )

    @extend_schema(
        tags=["catalog"],
        summary="Change pricing",
        request=PriceChangeSerializer,
        responses={200: PriceHistorySerializer},
    )
    @action(detail=True, methods=["post"], url_path="change-price")
    def change_price(self, request, pk=None):
        medicine = self.get_object()
        serializer = PriceChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        history = services.change_price(
            medicine, actor=request.user, **serializer.validated_data
        )
        return Response(PriceHistorySerializer(history).data)

    @extend_schema(
        tags=["catalog"],
        summary="Price history",
        responses={200: PriceHistorySerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="price-history")
    def price_history(self, request, pk=None):
        medicine = self.get_object()
        page = self.paginate_queryset(medicine.price_history.all())
        serializer = PriceHistorySerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(tags=["catalog"], summary="Current stock for this product")
    @action(detail=True, methods=["get"])
    def stock(self, request, pk=None):
        """Live stock position across batches, oldest expiry first."""
        from django.utils import timezone

        from apps.inventory.models import BatchStatus, StockBatch

        medicine = self.get_object()
        batches = (
            StockBatch.objects.filter(
                product=medicine, deleted_at__isnull=True, quantity_remaining__gt=0,
            )
            .select_related("warehouse")
            .order_by("expiry_date")
        )

        today = timezone.localdate()
        return Response(
            {
                "product_code": medicine.product_code,
                "name": medicine.name,
                "total_on_hand": sum(b.quantity_remaining for b in batches),
                "total_available": sum(
                    b.quantity_available
                    for b in batches
                    if b.status == BatchStatus.ACTIVE and b.expiry_date >= today
                ),
                "reorder_level": medicine.reorder_level,
                "batches": [
                    {
                        "id": str(b.id),
                        "batch_number": b.batch_number,
                        "warehouse": b.warehouse.code,
                        "expiry_date": b.expiry_date,
                        "days_to_expiry": b.days_to_expiry,
                        "quantity_remaining": str(b.quantity_remaining),
                        "quantity_available": str(b.quantity_available),
                        "status": b.status,
                        "is_expired": b.is_expired,
                    }
                    for b in batches
                ],
            }
        )


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.filter(deleted_at__isnull=True).select_related("parent")
    serializer_class = CategorySerializer
    search_fields = ["code", "name_fr", "name_en"]
    ordering = ["code"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "catalog.view_medicine",
        "retrieve": "catalog.view_medicine",
        "create": "catalog.manage_categories",
        "update": "catalog.manage_categories",
        "partial_update": "catalog.manage_categories",
        "destroy": "catalog.manage_categories",
    }


class ManufacturerViewSet(viewsets.ModelViewSet):
    queryset = Manufacturer.objects.filter(deleted_at__isnull=True)
    serializer_class = ManufacturerSerializer
    search_fields = ["code", "name", "country"]
    ordering = ["name"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "catalog.view_medicine",
        "retrieve": "catalog.view_medicine",
        "create": "catalog.manage_manufacturers",
        "update": "catalog.manage_manufacturers",
        "partial_update": "catalog.manage_manufacturers",
        "destroy": "catalog.manage_manufacturers",
    }


class UnitOfMeasureViewSet(viewsets.ModelViewSet):
    queryset = UnitOfMeasure.objects.filter(deleted_at__isnull=True)
    serializer_class = UnitOfMeasureSerializer
    # Without these the `search` parameter was accepted and silently ignored,
    # so the units screen's search box did nothing and a client checking for an
    # existing code before creating one always saw the whole table.
    search_fields = ["code", "name_fr", "name_en"]
    ordering = ["code"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "catalog.view_medicine",
        "retrieve": "catalog.view_medicine",
        "create": "catalog.manage_categories",
        "update": "catalog.manage_categories",
        "partial_update": "catalog.manage_categories",
        "destroy": "catalog.manage_categories",
    }
