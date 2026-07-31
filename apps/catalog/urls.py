from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import CategoryViewSet, ManufacturerViewSet, MedicineViewSet, UnitOfMeasureViewSet

router = DefaultRouter()
router.register("medicines", MedicineViewSet, basename="medicine")
router.register("categories", CategoryViewSet, basename="category")
router.register("manufacturers", ManufacturerViewSet, basename="manufacturer")
router.register("units", UnitOfMeasureViewSet, basename="unit-of-measure")

urlpatterns = [path("", include(router.urls))]
