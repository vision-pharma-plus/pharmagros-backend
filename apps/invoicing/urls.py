from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import InvoiceViewSet, PaymentReceiptViewSet, PaymentViewSet

router = DefaultRouter()
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("payments", PaymentViewSet, basename="payment")
router.register("receipts", PaymentReceiptViewSet, basename="payment-receipt")

urlpatterns = [path("", include(router.urls))]
