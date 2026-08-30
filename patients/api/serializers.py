from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from patients.models import Patient
from patients.services import create_patient, validate_date_of_birth


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = [
            "id",
            "clinic",
            "patient_number",
            "first_name",
            "last_name",
            "phone",
            "email",
            "date_of_birth",
            "gender",
            "blood_type",
            "national_id",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "clinic", "patient_number", "is_active", "created_at", "updated_at"]
        # national_id participates in a composite UniqueConstraint (unique_national_id_per_clinic_when_set),
        # which makes DRF force required=True by default even though the model field is blank=True.
        # business/validation-rules.md: "National ID: Optional. Must be unique if provided."
        extra_kwargs = {"national_id": {"required": False}}

    def validate_date_of_birth(self, value):
        validate_date_of_birth(value)
        return value

    def create(self, validated_data):
        clinic = validated_data.pop("clinic")
        try:
            return create_patient(clinic=clinic, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
