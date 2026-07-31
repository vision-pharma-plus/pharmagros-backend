from rest_framework import serializers

from .models import AuditLog, DocumentSequence


class AuditLogSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id", "uuid", "timestamp", "actor", "actor_username", "actor_role",
            "action", "action_display", "entity_type", "entity_id", "entity_label",
            "previous_value", "new_value", "changed_fields",
            "ip_address", "user_agent", "request_id", "notes",
        ]
        # Every field is read-only: the API must offer no write path to the
        # audit trail at all, not even one guarded by permissions.
        read_only_fields = fields


class DocumentSequenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentSequence
        fields = ["id", "key", "label", "prefix", "padding", "scope", "current_value"]
        read_only_fields = ["id", "key", "current_value"]
