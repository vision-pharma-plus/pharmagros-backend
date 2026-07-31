from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import (
    BulkReceiveStockView,
    ReceiveStockView,
    ReconciliationView,
    StockBatchViewSet,
    StockLevelView,
    StockMovementViewSet,
    ValuationView,
    WarehouseViewSet,
)

router = DefaultRouter()
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("batches", StockBatchViewSet, basename="stock-batch")
router.register("movements", StockMovementViewSet, basename="stock-movement")

urlpatterns = [
    path("receive/", ReceiveStockView.as_view(), name="receive-stock"),
    path("receive/bulk/", BulkReceiveStockView.as_view(), name="receive-stock-bulk"),
    path("stock-levels/", StockLevelView.as_view(), name="stock-levels"),
    path("valuation/", ValuationView.as_view(), name="inventory-valuation"),
    path("reconciliation/", ReconciliationView.as_view(), name="stock-reconciliation"),
    path("", include(router.urls)),
]
