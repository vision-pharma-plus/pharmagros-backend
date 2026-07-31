from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness: the process is up. Deliberately touches no dependency."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(responses={200: dict}, tags=["ops"])
    def get(self, request):
        return Response({"status": "ok"})


class ReadinessView(APIView):
    """
    Readiness: the process can serve traffic.

    Separate from liveness so a transient database blip does not cause the
    orchestrator to kill an otherwise healthy container — it should stop
    routing traffic, then resume when the dependency recovers.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(responses={200: dict, 503: dict}, tags=["ops"])
    def get(self, request):
        checks: dict[str, str] = {}
        healthy = True

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc.__class__.__name__}"
            healthy = False

        try:
            cache.set("readiness-probe", "1", timeout=5)
            checks["cache"] = "ok" if cache.get("readiness-probe") == "1" else "degraded"
            if checks["cache"] != "ok":
                healthy = False
        except Exception as exc:
            checks["cache"] = f"error: {exc.__class__.__name__}"
            healthy = False

        return Response(
            {"status": "ready" if healthy else "not-ready", "checks": checks},
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
