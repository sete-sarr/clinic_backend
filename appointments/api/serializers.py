from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from appointments.models import Appointment
from appointments.services import create_appointment, update_appointment
from patients.models import Patient


class AppointmentSerializer(serializers.ModelSerializer):
    # Facilités en lecture seule pour les UI de liste/détail — réutilisent le select_related
    # ("patient", "doctor__user") déjà présent sur le queryset (voir AppointmentViewSet), donc
    # n'ajoutent aucune requête supplémentaire.
    patient_display = serializers.SerializerMethodField()
    doctor_display = serializers.SerializerMethodField()
    # Non requis au niveau du champ : un appelant avec le rôle patient l'omet et la vue le résout
    # côté serveur à partir de request.user.patient_profile (AppointmentViewSet.perform_create).
    # Les appelants staff/médecin doivent quand même le fournir — c'est vérifié dans
    # perform_create, pas ici.
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
