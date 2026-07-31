from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import CustomerContactViewSet, CustomerViewSet, SupplierViewSet

router = DefaultRouter()
router.register("customers", CustomerViewSet, basename="customer")
router.register("customer-contacts", CustomerContactViewSet, basename="customer-contact")
router.register("suppliers", SupplierViewSet, basename="supplier")

urlpatterns = [path("", include(router.urls))]
