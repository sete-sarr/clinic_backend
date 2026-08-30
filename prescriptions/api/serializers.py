from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from doctors.models import Doctor
from prescriptions.models import Prescription, PrescriptionItem
from prescriptions.services import create_prescription, update_prescription


class PrescriptionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrescriptionItem
        fields = ["id", "medication_name", "dosage", "frequency", "duration", "quantity", "instructions"]
        read_only_fields = ["id"]


class PrescriptionSerializer(serializers.ModelSerializer):
    items = PrescriptionItemSerializer(many=True)
    # Read-only conveniences for list/detail UIs — reuse the queryset's existing
    # select_related("patient", "doctor__user") (see PrescriptionViewSet), so these add no extra queries.
    patient_display = serializers.SerializerMethodField()
    doctor_display = serializers.SerializerMethodField()
    # Optional on input: PrescriptionViewSet.perform_create always overrides this with the
    # requesting doctor's own profile, and clinic_admin picks one explicitly from the UI.
    doctor = serializers.PrimaryKeyRelatedField(queryset=Doctor.objects.all(), required=False)

    class Meta:
        model = Prescription
        fields = [
            "id",
            "clinic",
            "consultation",
            "patient",
            "patient_display",
            "doctor",
            "doctor_display",
            "status",
            "notes",
            "items",
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
        items = validated_data.pop("items")
        try:
            return create_prescription(clinic=clinic, items=items, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def update(self, instance, validated_data):
        validated_data.pop("clinic", None)
        items = validated_data.pop("items", None)
        try:
            return update_prescription(prescription=instance, items=items, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
