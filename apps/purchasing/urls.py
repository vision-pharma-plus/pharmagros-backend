from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import (
    GoodsReceiptViewSet,
    PurchaseOrderViewSet,
    SupplierInvoiceViewSet,
    SupplierPaymentViewSet,
)

router = DefaultRouter()
router.register("orders", PurchaseOrderViewSet, basename="purchase-order")
router.register("receipts", GoodsReceiptViewSet, basename="goods-receipt")
router.register("supplier-invoices", SupplierInvoiceViewSet, basename="supplier-invoice")
router.register("supplier-payments", SupplierPaymentViewSet, basename="supplier-payment")

urlpatterns = [path("", include(router.urls))]
