from django.db.models import Q
from django.utils import timezone
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import HasPermission

from . import services
from .models import Announcement, Notification


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)
    code_display = serializers.CharField(source="get_code_display", read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id", "code", "code_display", "severity", "title", "body", "link",
            "entity_type", "entity_id", "read_at", "is_read",
            "dismissed_at", "created_at",
        ]
        read_only_fields = fields


class AnnouncementSerializer(serializers.ModelSerializer):
    is_visible = serializers.BooleanField(read_only=True)

    class Meta:
        model = Announcement
        fields = [
            "id", "title_fr", "title_en", "body_fr", "body_en",
            "severity", "target_roles", "starts_at", "ends_at",
            "is_published", "is_visible", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class NotificationFilter(filters.FilterSet):
    unread = filters.BooleanFilter(method="filter_unread")

    class Meta:
        model = Notification
        fields = ["code", "severity"]

    def filter_unread(self, queryset, name, value):
        return (
            queryset.filter(read_at__isnull=True)
            if value
            else queryset.filter(read_at__isnull=False)
        )


@extend_schema_view(
    list=extend_schema(tags=["notifications"], summary="List my notifications"),
    retrieve=extend_schema(tags=["notifications"], summary="Retrieve a notification"),
)
class NotificationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """
    A user's own notifications.

    Permission is identity-based rather than RBAC-based: every authenticated
    user sees their own alerts and nobody else's, enforced by the queryset
    filter rather than by a permission code.
    """

    serializer_class = NotificationSerializer
    filterset_class = NotificationFilter
    ordering = ["-created_at"]
    permission_classes = [IsAuthenticated]
    # Declared so drf-spectacular can infer the model during schema
    # generation, when there is no authenticated request to filter by.
    queryset = Notification.objects.none()

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Notification.objects.none()
        return Notification.objects.filter(recipient=user, dismissed_at__isnull=True)

    @extend_schema(tags=["notifications"], summary="Unread count")
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": services.unread_count(request.user)})

    @extend_schema(tags=["notifications"], summary="Mark one notification as read")
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.mark_read()
        return Response(NotificationSerializer(notification).data)

    @extend_schema(tags=["notifications"], summary="Mark all as read")
    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        return Response({"marked": services.mark_all_read(request.user)})

    @extend_schema(tags=["notifications"], summary="Dismiss a notification")
    @action(detail=True, methods=["post"])
    def dismiss(self, request, pk=None):
        notification = self.get_object()
        notification.dismissed_at = timezone.now()
        notification.save(update_fields=["dismissed_at", "updated_at"])
        return Response({"detail": "Dismissed."})


class AnnouncementViewSet(viewsets.ModelViewSet):
    """
    System announcements.

    Reading is open to any authenticated user; writing requires the
    announcement management permission.
    """

    queryset = Announcement.objects.filter(deleted_at__isnull=True).prefetch_related(
        "target_roles"
    )
    serializer_class = AnnouncementSerializer
    filterset_fields = ["severity", "is_published"]
    ordering = ["-starts_at"]
    permission_classes = [HasPermission]
    required_permissions = {
        # None means "any authenticated user" — announcements are meant to be
        # seen by everyone they target.
        "list": None,
        "retrieve": None,
        "active": None,
        "create": "notifications.manage_announcements",
        "update": "notifications.manage_announcements",
        "partial_update": "notifications.manage_announcements",
        "destroy": "notifications.manage_announcements",
    }

    @extend_schema(tags=["notifications"], summary="Announcements visible to me now")
    @action(detail=False, methods=["get"])
    def active(self, request):
        """
        Currently visible announcements for this user.

        Filtered by role: an announcement with no target roles is a broadcast;
        one with targets reaches only users holding those roles.
        """
        now = timezone.now()
        user_role_ids = list(request.user.roles.values_list("id", flat=True))

        # No end date means open-ended; otherwise it must still be in future.
        still_running = Q(ends_at__isnull=True) | Q(ends_at__gt=now)

        announcements = (
            self.get_queryset()
            .filter(is_published=True, starts_at__lte=now)
            .filter(still_running)
        )
        visible = [
            a
            for a in announcements
            if not a.target_roles.exists()
            or a.target_roles.filter(id__in=user_role_ids).exists()
        ]
        return Response(AnnouncementSerializer(visible, many=True).data)
