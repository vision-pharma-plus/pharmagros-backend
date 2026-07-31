from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .audit import record, verify_chain
from .models import AuditAction, AuditLog, DocumentSequence
from .permissions import HasPermission
from .serializers import AuditLogSerializer, DocumentSequenceSerializer


class AuditLogFilter(filters.FilterSet):
    date_from = filters.DateTimeFilter(field_name="timestamp", lookup_expr="gte")
    date_to = filters.DateTimeFilter(field_name="timestamp", lookup_expr="lte")
    action = filters.ChoiceFilter(choices=AuditAction.choices)
    actor_username = filters.CharFilter(lookup_expr="icontains")
    entity_type = filters.CharFilter(lookup_expr="icontains")

    class Meta:
        model = AuditLog
        fields = ["action", "entity_type", "entity_id", "actor", "actor_username", "request_id"]


@extend_schema_view(
    list=extend_schema(tags=["audit"], summary="List audit entries"),
    retrieve=extend_schema(tags=["audit"], summary="Retrieve an audit entry"),
)
class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Read-only access to the audit trail.

    Only List and Retrieve mixins are wired: there is deliberately no create,
    update or destroy route, so the trail cannot be written through the API
    under any permission configuration.
    """

    queryset = AuditLog.objects.select_related("actor").all()
    serializer_class = AuditLogSerializer
    filterset_class = AuditLogFilter
    search_fields = ["actor_username", "entity_label", "entity_id", "notes"]
    ordering_fields = ["timestamp", "action", "entity_type"]
    ordering = ["-timestamp"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "core.view_auditlog",
        "retrieve": "core.view_auditlog",
        "verify": "core.verify_auditlog",
        "export": "core.export_auditlog",
    }

    @extend_schema(tags=["audit"], summary="Verify audit chain integrity")
    @action(detail=False, methods=["post"])
    def verify(self, request):
        """On-demand tamper-evidence check, for inspections."""
        result = verify_chain()
        record(
            AuditAction.EXPORT,
            "core.AuditLog",
            notes=f"Manual chain verification: valid={result['valid']}, checked={result['checked']}",
        )
        return Response(result)


class DocumentSequenceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Sequence configuration.

    Prefix and padding are editable; `current_value` is not exposed for
    writing, because rewinding a fiscal counter would produce duplicate
    invoice numbers.
    """

    queryset = DocumentSequence.objects.all()
    serializer_class = DocumentSequenceSerializer
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "core.view_documentsequence",
        "retrieve": "core.view_documentsequence",
        "update": "core.change_documentsequence",
        "partial_update": "core.change_documentsequence",
    }
