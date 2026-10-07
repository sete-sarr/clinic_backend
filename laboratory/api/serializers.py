from django.utils.translation import gettext as _
from rest_framework import serializers

from common.permissions import in_role
from consultations.models import Consultation
from laboratory.models import LabOrder, LabResult, LabTest
from patients.models import Patient


class LabTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabTest
        fields = [
            "id", "code", "name", "price", "unit", "reference_min", "reference_max",
            "is_active", "archived_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "is_active", "archived_at", "created_at", "updated_at"]

    def validate_code(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError(_("Le code est obligatoire."))
        return value

    def validate(self, attrs):
        reference_min = attrs.get("reference_min", getattr(self.instance, "reference_min", None))
        reference_max = attrs.get("reference_max", getattr(self.instance, "reference_max", None))
        if reference_min is not None and reference_max is not None and reference_min > reference_max:
            raise serializers.ValidationError(
                {"reference_max": _("La valeur maximale doit être supérieure ou égale à la valeur minimale.")}
            )
        return attrs


def _user_display(user):
    return (user.get_full_name() or user.get_username()) if user else ""


class LabResultSerializer(serializers.ModelSerializer):
    entered_by_display = serializers.SerializerMethodField()
    attachment_name = serializers.SerializerMethodField()

    class Meta:
        model = LabResult
        fields = [
            "id", "value", "is_abnormal", "comment", "entered_by_display", "entered_at",
            "correction_reason", "superseded_at", "attachment_name",
        ]

    def get_entered_by_display(self, obj):
        return _user_display(obj.entered_by)

    def get_attachment_name(self, obj):
        attachment = getattr(obj, "attachment", None)
        return attachment.filename if attachment else ""


class LabOrderSerializer(serializers.ModelSerializer):
    """Lecture d'une demande, filtrée selon le rôle (business/access-policy.md) :
    - technicien : jamais la facture, la consultation ni les prix ;
    - patient : ni le renseignement clinique, ni la consultation, ni l'historique des corrections."""

    patient_display = serializers.SerializerMethodField()
    doctor_display = serializers.SerializerMethodField()
    validated_by_display = serializers.SerializerMethodField()
    invoice_number = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()
    has_abnormal = serializers.SerializerMethodField()

    class Meta:
        model = LabOrder
        fields = [
            "id", "number", "patient", "patient_display", "doctor", "doctor_display", "consultation",
            "status", "clinical_note", "items", "has_abnormal", "collected_at", "completed_at",
            "validated_at", "validated_by_display", "cancelled_at", "invoice", "invoice_number",
            "created_at", "updated_at",
        ]

    def _roles(self):
        user = self.context["request"].user
        return {
            "finance": user.is_superuser or in_role(user, "doctor", "clinic_admin"),
            "patient": in_role(user, "patient") and not in_role(user, "doctor", "lab_technician", "clinic_admin"),
        }

    def get_patient_display(self, obj):
        return f"{obj.patient.first_name} {obj.patient.last_name} ({obj.patient.patient_number})"

    def get_doctor_display(self, obj):
        return _user_display(obj.doctor.user)

    def get_validated_by_display(self, obj):
        return _user_display(obj.validated_by)

    def get_invoice_number(self, obj):
        return obj.invoice.number if obj.invoice_id else ""

    def get_has_abnormal(self, obj):
        return any(item.current_result and item.current_result.is_abnormal for item in obj.items.all())

    def get_items(self, obj):
        roles = self._roles()
        rows = []
        for item in obj.items.all():
            results = list(item.results.all())
            current = next((result for result in results if result.superseded_at is None), None)
            row = {
                "id": item.id, "test": item.test_id, "test_code": item.test_code, "test_name": item.test_name,
                "unit": item.unit, "reference_min": item.reference_min, "reference_max": item.reference_max,
                "result": LabResultSerializer(current).data if current else None,
            }
            if roles["finance"] or roles["patient"]:
                row["price"] = item.price
            if not roles["patient"]:
                row["history"] = LabResultSerializer([r for r in results if r.superseded_at], many=True).data
            rows.append(row)
        return rows

    def to_representation(self, instance):
        data = super().to_representation(instance)
        roles = self._roles()
        if not roles["finance"]:
            data.pop("invoice")
            data.pop("invoice_number")
        if not roles["finance"] or roles["patient"]:
            data.pop("consultation")
        if roles["patient"]:
            data.pop("clinical_note")
        return data


class LabOrderCreateSerializer(serializers.Serializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=Patient.objects.all())
    consultation = serializers.PrimaryKeyRelatedField(queryset=Consultation.objects.all(), required=False, allow_null=True)
    tests = serializers.PrimaryKeyRelatedField(queryset=LabTest.objects.all(), many=True)
    clinical_note = serializers.CharField(required=False, allow_blank=True, default="")


class ResultEntrySerializer(serializers.Serializer):
    item = serializers.IntegerField()
    value = serializers.CharField(max_length=100, allow_blank=True)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class RecordResultsSerializer(serializers.Serializer):
    results = ResultEntrySerializer(many=True, allow_empty=False)


class CorrectResultSerializer(serializers.Serializer):
    value = serializers.CharField(max_length=100)
    comment = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField()


class AttachmentSerializer(serializers.Serializer):
    file = serializers.FileField()
