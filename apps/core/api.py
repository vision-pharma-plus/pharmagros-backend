from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import record, verify_chain
from .exceptions import ServiceUnavailable
from .models import AuditAction, AuditLog, DocumentSequence
from .permissions import HasPermission
from .serializers import (
    AuditLogSerializer,
    DocumentSequenceSerializer,
    TranslationRequestSerializer,
    TranslationResponseSerializer,
)
from .translation import TranslationUnavailable, translate_on_demand


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

    @extend_schema(
        tags=["audit"],
        summary="Export the audit trail",
        parameters=[
            OpenApiParameter(
                "export", str, enum=["csv", "xlsx"],
                description="Export format; defaults to CSV.",
            ),
        ],
        responses={200: OpenApiTypes.BINARY},
    )
    @action(detail=False, methods=["get"])
    def export(self, request):
        """
        Download the filtered audit trail.

        `core.export_auditlog` was already defined, granted to the Auditor and
        Administrator roles, and declared on this viewset — but no route
        implemented it, so the permission governed nothing and an auditor
        asked for the trail in a portable form had no way to obtain one. The
        same filters as `list` apply, so an inspection can be scoped to a date
        range or an entity rather than pulling the whole table.

        Exporting the trail is itself an audited event: taking a copy of who
        did what is exactly the sort of act the trail exists to record.
        """
        from apps.reporting.exporters import export as export_rows

        fmt = request.query_params.get("export", "csv")
        if fmt not in {"csv", "xlsx"}:
            raise ValidationError(
                {"export": _("Unsupported export format. Use csv or xlsx.")}
            )

        queryset = self.filter_queryset(self.get_queryset())
        rows = [
            {
                "timestamp": entry.timestamp,
                "actor": entry.actor_username,
                "action": entry.get_action_display(),
                "entity_type": entry.entity_type,
                "entity_label": entry.entity_label,
                "entity_id": entry.entity_id,
                "ip_address": entry.ip_address,
                "notes": entry.notes,
            }
            # `.iterator()` keeps a long trail from being materialised at once;
            # this table is append-only and grows without bound.
            for entry in queryset.iterator(chunk_size=2000)
        ]

        record(
            AuditAction.EXPORT,
            "core.AuditLog",
            notes=f"Audit trail exported ({fmt}, {len(rows)} entries)",
        )

        return export_rows(
            fmt,
            rows,
            [
                ("timestamp", _("Timestamp")),
                ("actor", _("User")),
                ("action", _("Action")),
                ("entity_type", _("Entity type")),
                ("entity_label", _("Entity")),
                ("entity_id", _("Entity ID")),
                ("ip_address", _("IP address")),
                ("notes", _("Notes")),
            ],
            "audit-trail",
            title=_("Audit trail"),
        )


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


class TranslateView(APIView):
    """
    On-demand machine translation of user-entered text, for display only.

    Staff write notes, reasons and comments in whichever language they work
    in, and a colleague reading the record later may not share it. Asking
    writers to translate before saving is not workable at a counter, so the
    translation happens on the reading side, when and only when a reader asks
    for it.

    Read-only by construction: the request carries the text itself and the
    response is never persisted. The stored note remains the record of truth,
    which matters because several of these fields (cancellation and override
    reasons) are audit evidence — a machine translation must never become the
    stored version of one.

    Any authenticated user may call it: the reader already has the original
    text on screen, so translating it discloses nothing they cannot read.
    Throttled because each miss is a paid upstream call.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "translate"

    @extend_schema(
        tags=["core"],
        summary="Translate user-entered text for display",
        request=TranslationRequestSerializer,
        responses={200: TranslationResponseSerializer},
    )
    def post(self, request):
        serializer = TranslationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        text = serializer.validated_data["text"]
        target = serializer.validated_data["target"]

        try:
            result = translate_on_demand(text, target)
        except TranslationUnavailable as exc:
            # 503 rather than a silent echo of the source text: a reader who
            # asked for a translation must not be shown the untranslated
            # original as though it were one.
            raise ServiceUnavailable(
                _("Translation is unavailable right now. Please try again later.")
            ) from exc

        return Response(
            TranslationResponseSerializer(
                {
                    "translated": result.text,
                    "target": target,
                    "engine": result.engine,
                    "cached": result.cached,
                }
            ).data
        )
