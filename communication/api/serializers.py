from rest_framework import serializers

from communication.models import InAppNotification


class InAppNotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = InAppNotification
        fields = ["id", "category", "priority", "title", "body", "link", "is_read", "read_at", "archived_at", "created_at"]
        read_only_fields = fields

    def get_is_read(self, obj):
        return obj.read_at is not None
