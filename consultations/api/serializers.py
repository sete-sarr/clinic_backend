from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from consultations.models import Consultation
from consultations.services import create_consultation, update_consultation
from doctors.models import Doctor


class ConsultationSerializer(serializers.ModelSerializer):
    # Commodités en lecture seule pour les UI de liste/détail — réutilisent le select_related
    # ("patient", "doctor__user") déjà présent sur le queryset (voir ConsultationViewSet), donc
    # elles n'ajoutent aucune requête supplémentaire.
    patient_display = serializers.SerializerMethodField()
    doctor_display = serializers.SerializerMethodField()
    # Optionnel en entrée : ConsultationViewSet.perform_create écrase toujours ce champ avec le
    # profil du médecin demandeur, et clinic_admin en choisit un explicitement depuis l'UI.
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
