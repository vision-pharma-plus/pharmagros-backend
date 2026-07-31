from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import RecallTraceView, SaleReturnViewSet, SaleViewSet

router = DefaultRouter()
router.register("sales", SaleViewSet, basename="sale")
router.register("returns", SaleReturnViewSet, basename="sale-return")

urlpatterns = [
    path("recall-trace/", RecallTraceView.as_view(), name="recall-trace"),
    path("", include(router.urls)),
]
