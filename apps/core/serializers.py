from rest_framework import serializers

from .models import AuditLog, DocumentSequence
from .translation import LANGUAGE_CHOICES, MAX_TEXT_LENGTH


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


class TranslationRequestSerializer(serializers.Serializer):
    """
    Input for the on-demand translation of user-entered text.

    Takes the text itself rather than a model reference: the control is a
    reading aid over whatever note is already on the reader's screen, and
    accepting an entity id would mean granting this endpoint read access to
    every table that has a notes field.
    """

    text = serializers.CharField(max_length=MAX_TEXT_LENGTH, trim_whitespace=True)
    target = serializers.ChoiceField(choices=LANGUAGE_CHOICES)


class TranslationResponseSerializer(serializers.Serializer):
    """Response shape, declared so the OpenAPI schema documents it."""

    translated = serializers.CharField()
    target = serializers.CharField()
    engine = serializers.CharField()
    cached = serializers.BooleanField()
