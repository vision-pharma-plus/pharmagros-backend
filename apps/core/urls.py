from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import AuditLogViewSet, DocumentSequenceViewSet, TranslateView

router = DefaultRouter()
router.register("audit-logs", AuditLogViewSet, basename="audit-log")
router.register("sequences", DocumentSequenceViewSet, basename="sequence")

urlpatterns = [
    path("translate/", TranslateView.as_view(), name="translate"),
    path("", include(router.urls)),
]
