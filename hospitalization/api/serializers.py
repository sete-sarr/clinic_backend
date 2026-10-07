from django.utils.translation import gettext as _
from rest_framework import serializers

from common.permissions import in_role
from departments.models import Department
from hospitalization.models import Admission, Bed, NursingNote, Room, RoomType, VitalSign
from patients.models import Patient


def _user_display(user):
    return (user.get_full_name() or user.get_username()) if user else ""


def _sees_rates(request):
    """Tarifs de nuitée : administrateur et comptabilité uniquement (jamais l'infirmier)."""
    user = request.user
    return user.is_superuser or in_role(user, "clinic_admin", "accountant")


class TenantRelatedField(serializers.PrimaryKeyRelatedField):
    """Clé étrangère limitée à la clinique de l'utilisateur : un identifiant d'une autre clinique
    est simplement « invalide » (pas d'oracle d'existence inter-cliniques)."""

    def get_queryset(self):
        user = self.context["request"].user
        return super().get_queryset().filter(clinic_id=user.clinic_id)


def _check_unique(serializer, queryset, message):
    """Unicité par clinique (contrainte en base) vérifiée avant l'écriture pour un message clair."""
    if serializer.instance is not None:
        queryset = queryset.exclude(pk=serializer.instance.pk)
    if queryset.filter(clinic_id=serializer.context["request"].user.clinic_id).exists():
        raise serializers.ValidationError(message)


class RoomTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoomType
        fields = ["id", "name", "nightly_rate", "is_active"]
        read_only_fields = ["id", "is_active"]

    def validate_name(self, value):
        value = value.strip()
        _check_unique(self, RoomType.objects.filter(name__iexact=value), _("Ce type de chambre existe déjà."))
        return value

    def validate_nightly_rate(self, value):
        if value <= 0:
            raise serializers.ValidationError(_("Le tarif de nuitée doit être strictement positif."))
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not _sees_rates(self.context["request"]):
            data.pop("nightly_rate")
        return data


class RoomSerializer(serializers.ModelSerializer):
    department = TenantRelatedField(queryset=Department.objects.filter(is_active=True))
    room_type = TenantRelatedField(queryset=RoomType.objects.filter(is_active=True))
    department_name = serializers.CharField(source="department.name", read_only=True)
    room_type_name = serializers.CharField(source="room_type.name", read_only=True)

    class Meta:
        model = Room
        fields = ["id", "number", "department", "department_name", "room_type", "room_type_name", "is_active"]
        read_only_fields = ["id", "is_active"]

    def validate_number(self, value):
        value = value.strip()
        _check_unique(self, Room.objects.filter(number__iexact=value), _("Une chambre porte déjà ce numéro."))
        return value


class CurrentStaySerializer(serializers.ModelSerializer):
    """Séjour en cours sur un lit : identité et numéro uniquement (emplacement, sans motif)."""

    patient_display = serializers.SerializerMethodField()

    class Meta:
        model = Admission
        fields = ["id", "number", "patient_display"]

    def get_patient_display(self, obj):
        return f"{obj.patient.first_name} {obj.patient.last_name} ({obj.patient.patient_number})"


class BedSerializer(serializers.ModelSerializer):
    room = TenantRelatedField(queryset=Room.objects.filter(is_active=True))
    room_number = serializers.CharField(source="room.number", read_only=True)
    room_type_name = serializers.CharField(source="room.room_type.name", read_only=True)
    department = serializers.IntegerField(source="room.department_id", read_only=True)
    department_name = serializers.CharField(source="room.department.name", read_only=True)
    current_stay = serializers.SerializerMethodField()

    class Meta:
        model = Bed
        fields = [
            "id", "label", "status", "room", "room_number", "room_type_name", "department", "department_name",
            "is_active", "current_stay",
        ]
        read_only_fields = ["id", "status", "is_active"]

    def validate(self, attrs):
        room = attrs.get("room", getattr(self.instance, "room", None))
        label = attrs.get("label", getattr(self.instance, "label", "")).strip()
        duplicates = Bed.objects.filter(room=room, label__iexact=label)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError({"label": _("Ce lit existe déjà dans la chambre.")})
        attrs["label"] = label
        return attrs

    def get_current_stay(self, obj):
        stays = getattr(obj, "current_stays", None)
        return CurrentStaySerializer(stays[0]).data if stays else None


class BedTransferSerializer(serializers.Serializer):
    from_bed = serializers.SerializerMethodField()
    to_bed = serializers.SerializerMethodField()
    transferred_at = serializers.DateTimeField()
    transferred_by = serializers.SerializerMethodField()
    reason = serializers.CharField()

    def _bed(self, bed):
        return f"{bed.room.number} — {bed.label}" if bed else ""

    def get_from_bed(self, obj):
        return self._bed(obj.from_bed)

    def get_to_bed(self, obj):
        return self._bed(obj.to_bed)

    def get_transferred_by(self, obj):
        return _user_display(obj.transferred_by)


class AdmissionSerializer(serializers.ModelSerializer):
    """Lecture d'un séjour, filtrée selon le rôle (business/access-policy.md, permissions-matrix.md) :
    - réception : emplacement uniquement ; comptabilité : dates, nuitées et facture — jamais le motif ;
    - infirmier : tout le clinique, jamais la facture."""

    patient_display = serializers.SerializerMethodField()
    doctor_display = serializers.SerializerMethodField()
    department_name = serializers.CharField(source="department.name", read_only=True)
    room_number = serializers.SerializerMethodField()
    bed_label = serializers.SerializerMethodField()
    admitted_by_display = serializers.SerializerMethodField()
    discharged_by_display = serializers.SerializerMethodField()
    invoice_number = serializers.SerializerMethodField()
    transfers = BedTransferSerializer(many=True, read_only=True)

    CLINICAL_FIELDS = ("doctor", "doctor_display", "reason", "discharge_summary", "admitted_by_display",
                       "discharged_by_display", "transfers")
    FINANCE_FIELDS = ("invoice", "invoice_number")
    ACCOUNTING_FIELDS = ("nights",)

    class Meta:
        model = Admission
        fields = [
            "id", "number", "patient", "patient_display", "doctor", "doctor_display", "department",
            "department_name", "bed", "room_number", "bed_label", "status", "reason", "planned_for",
            "admitted_at", "admitted_by_display", "discharged_at", "discharged_by_display", "discharge_summary",
            "nights", "invoice", "invoice_number", "transfers", "created_at",
        ]

    def get_patient_display(self, obj):
        return f"{obj.patient.first_name} {obj.patient.last_name} ({obj.patient.patient_number})"

    def get_doctor_display(self, obj):
        return _user_display(obj.doctor.user)

    def get_room_number(self, obj):
        return obj.bed.room.number if obj.bed_id else ""

    def get_bed_label(self, obj):
        return obj.bed.label if obj.bed_id else ""

    def get_admitted_by_display(self, obj):
        return _user_display(obj.admitted_by)

    def get_discharged_by_display(self, obj):
        return _user_display(obj.discharged_by)

    def get_invoice_number(self, obj):
        return obj.invoice.number if obj.invoice_id else ""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        user = self.context["request"].user
        clinical = user.is_superuser or in_role(user, "doctor", "nurse", "clinic_admin")
        finance = user.is_superuser or in_role(user, "doctor", "clinic_admin", "accountant")
        accounting = clinical or in_role(user, "accountant")
        for allowed, fields in ((clinical, self.CLINICAL_FIELDS), (finance, self.FINANCE_FIELDS),
                                (accounting, self.ACCOUNTING_FIELDS)):
            if not allowed:
                for field in fields:
                    data.pop(field, None)
        return data


class AdmissionCreateSerializer(serializers.Serializer):
    patient = TenantRelatedField(queryset=Patient.objects.all())
    department = TenantRelatedField(queryset=Department.objects.filter(is_active=True))
    reason = serializers.CharField()
    planned_for = serializers.DateField(required=False, allow_null=True)
    bed = TenantRelatedField(queryset=Bed.objects.all(), required=False, allow_null=True)


class BedChoiceSerializer(serializers.Serializer):
    bed = TenantRelatedField(queryset=Bed.objects.all())
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class DischargeSerializer(serializers.Serializer):
    summary = serializers.CharField(required=False, allow_blank=True, default="")


class VitalSignSerializer(serializers.ModelSerializer):
    recorded_by_display = serializers.SerializerMethodField()
    recorded_at = serializers.DateTimeField(required=False)

    class Meta:
        model = VitalSign
        fields = [
            "id", "recorded_at", "temperature", "systolic", "diastolic", "pulse", "respiratory_rate",
            "oxygen_saturation", "weight", "pain", "recorded_by_display",
        ]
        read_only_fields = ["id"]

    def get_recorded_by_display(self, obj):
        return _user_display(obj.recorded_by)


class NursingNoteSerializer(serializers.ModelSerializer):
    recorded_by_display = serializers.SerializerMethodField()

    class Meta:
        model = NursingNote
        fields = ["id", "note", "recorded_at", "recorded_by_display"]
        read_only_fields = ["id", "recorded_at"]

    def get_recorded_by_display(self, obj):
        return _user_display(obj.recorded_by)
