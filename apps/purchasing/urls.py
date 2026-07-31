from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import GoodsReceiptViewSet, PurchaseOrderViewSet

router = DefaultRouter()
router.register("orders", PurchaseOrderViewSet, basename="purchase-order")
router.register("receipts", GoodsReceiptViewSet, basename="goods-receipt")

urlpatterns = [path("", include(router.urls))]
