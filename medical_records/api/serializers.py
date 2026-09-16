from rest_framework import serializers

from medical_records.models import MedicalRecord


class MedicalRecordSerializer(serializers.ModelSerializer):
    # Champ pratique en lecture seule pour les UI liste/détail — réutilise le select_related("patient")
    # déjà présent dans le queryset (voir MedicalRecordViewSet), donc n'ajoute aucune requête supplémentaire.
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
