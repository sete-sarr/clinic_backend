from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from appointments.models import Appointment
from appointments.services import create_appointment, update_appointment
from patients.models import Patient


class AppointmentSerializer(serializers.ModelSerializer):
    # Read-only conveniences for list/detail UIs — reuse the queryset's existing
    # select_related("patient", "doctor__user") (see AppointmentViewSet), so these add no extra queries.
    patient_display = serializers.SerializerMethodField()
    doctor_display = serializers.SerializerMethodField()
    # Not required at the field level: a patient-role caller omits it and the view resolves it
    # server-side from request.user.patient_profile (AppointmentViewSet.perform_create). Staff/doctor
    # callers must still supply it — that's enforced in perform_create, not here.
    patient = serializers.PrimaryKeyRelatedField(queryset=Patient.objects.all(), required=False)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "clinic",
            "patient",
            "patient_display",
            "doctor",
            "doctor_display",
            "date",
            "time",
            "status",
            "reason",
            "day_before_reminder_sent_at",
            "checked_in_at",
            "ticket_number",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "clinic",
            "day_before_reminder_sent_at",
            "checked_in_at",
            "ticket_number",
            "created_at",
            "updated_at",
        ]

    def get_patient_display(self, obj):
        return f"{obj.patient.first_name} {obj.patient.last_name} ({obj.patient.patient_number})"

    def get_doctor_display(self, obj):
        return obj.doctor.user.get_full_name() or obj.doctor.user.get_username()

    def create(self, validated_data):
        clinic = validated_data.pop("clinic")
        try:
            return create_appointment(clinic=clinic, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def update(self, instance, validated_data):
        validated_data.pop("clinic", None)
        try:
            return update_appointment(appointment=instance, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
