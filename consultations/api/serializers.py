from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from consultations.models import Consultation
from consultations.services import create_consultation, update_consultation
from doctors.models import Doctor


class ConsultationSerializer(serializers.ModelSerializer):
    # Read-only conveniences for list/detail UIs — reuse the queryset's existing
    # select_related("patient", "doctor__user") (see ConsultationViewSet), so these add no extra queries.
    patient_display = serializers.SerializerMethodField()
    doctor_display = serializers.SerializerMethodField()
    # Optional on input: ConsultationViewSet.perform_create always overrides this with the
    # requesting doctor's own profile, and clinic_admin picks one explicitly from the UI.
    doctor = serializers.PrimaryKeyRelatedField(queryset=Doctor.objects.all(), required=False)

    class Meta:
        model = Consultation
        fields = [
            "id",
            "clinic",
            "patient",
            "patient_display",
            "doctor",
            "doctor_display",
            "appointment",
            "date",
            "is_follow_up",
            "chief_complaint",
            "diagnosis",
            "treatment_plan",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "clinic", "created_at", "updated_at"]

    def get_patient_display(self, obj):
        return f"{obj.patient.first_name} {obj.patient.last_name} ({obj.patient.patient_number})"

    def get_doctor_display(self, obj):
        return obj.doctor.user.get_full_name() or obj.doctor.user.get_username()

    def create(self, validated_data):
        clinic = validated_data.pop("clinic")
        try:
            return create_consultation(clinic=clinic, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def update(self, instance, validated_data):
        validated_data.pop("clinic", None)
        try:
            return update_consultation(consultation=instance, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
