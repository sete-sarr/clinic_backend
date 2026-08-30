from rest_framework import serializers

from common.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "user",
            "user_display",
            "clinic",
            "action",
            "model_name",
            "object_id",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields

    def get_user_display(self, obj):
        if not obj.user:
            return "System"
        return obj.user.get_full_name() or obj.user.get_username()
