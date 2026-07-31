from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import AuditLogViewSet, DocumentSequenceViewSet

router = DefaultRouter()
router.register("audit-logs", AuditLogViewSet, basename="audit-log")
router.register("sequences", DocumentSequenceViewSet, basename="sequence")

urlpatterns = [path("", include(router.urls))]
