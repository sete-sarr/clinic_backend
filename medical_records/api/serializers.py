from rest_framework import serializers

from medical_records.models import MedicalRecord


class MedicalRecordSerializer(serializers.ModelSerializer):
    # Read-only convenience for list/detail UIs — reuses the queryset's existing
    # select_related("patient") (see MedicalRecordViewSet), so this adds no extra queries.
    patient_display = serializers.SerializerMethodField()

    class Meta:
        model = MedicalRecord
        fields = [
            "id",
            "clinic",
            "patient",
            "patient_display",
            "allergies",
            "medical_history",
            "observations",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "clinic", "patient", "created_at", "updated_at"]

    def get_patient_display(self, obj):
        return f"{obj.patient.first_name} {obj.patient.last_name} ({obj.patient.patient_number})"
