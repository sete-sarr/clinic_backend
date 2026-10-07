from django.contrib.auth import get_user_model
from rest_framework import serializers

from hospitalization.api.serializers import TenantRelatedField
from hospitalization.models import Admission
from visitors.models import VisitLog


def _user_display(user):
    return (user.get_full_name() or user.get_username()) if user else ""


def admission_location(admission):
    """Patient hospitalisé visité : nom + service + chambre/lit, jamais le motif (docs/visitors.md §5)."""
    bed = admission.bed
    where = f"{admission.department.name} · {bed.room.number} — {bed.label}" if bed else admission.department.name
    return f"{admission.patient.first_name} {admission.patient.last_name} ({where})"


class VisitLogSerializer(serializers.ModelSerializer):
    visited_admission = TenantRelatedField(queryset=Admission.objects.all(), required=False, allow_null=True)
    visited_staff = TenantRelatedField(queryset=get_user_model().objects.all(), required=False, allow_null=True)
    visited_display = serializers.SerializerMethodField()
    checked_in_by_display = serializers.SerializerMethodField()
    checked_out_by_display = serializers.SerializerMethodField()
    is_present = serializers.SerializerMethodField()

    class Meta:
        model = VisitLog
        fields = [
            "id", "visitor_name", "visitor_phone", "visitor_type", "purpose", "visited_admission", "visited_staff",
            "visited_free_text", "visited_display", "checked_in_at", "checked_in_by_display", "checked_out_at",
            "checked_out_by_display", "auto_closed", "is_present",
        ]
        read_only_fields = ["id", "checked_in_at", "checked_out_at", "auto_closed"]

    def get_visited_display(self, obj):
        if obj.visited_admission_id:
            return admission_location(obj.visited_admission)
        if obj.visited_staff_id:
            return _user_display(obj.visited_staff)
        return obj.visited_free_text

    def get_checked_in_by_display(self, obj):
        return _user_display(obj.checked_in_by)

    def get_checked_out_by_display(self, obj):
        return _user_display(obj.checked_out_by)

    def get_is_present(self, obj):
        return obj.checked_out_at is None
