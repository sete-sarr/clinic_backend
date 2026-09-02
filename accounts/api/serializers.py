from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer as BaseTokenObtainPairSerializer

from accounts.models import User
from accounts.services import STAFF_ROLES_ASSIGNABLE
from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    doctor_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "clinic", "roles", "doctor_id"]

    def get_roles(self, obj):
        return list(obj.groups.values_list("name", flat=True))

    def get_doctor_id(self, obj):
        # Lets the frontend auto-fill/lock the doctor field on consultation and prescription
        # forms instead of asking a doctor to pick their own name from a list.
        doctor_profile = getattr(obj, "doctor_profile", None)
        return doctor_profile.id if doctor_profile else None


class TokenObtainPairSerializer(BaseTokenObtainPairSerializer):
    """Embeds clinic_id and roles in the JWT so the frontend never has to guess them."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["clinic_id"] = user.clinic_id
        token["roles"] = list(user.groups.values_list("name", flat=True))
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        # business/access-policy.md "AUDIT POLICY": Authentication is a logged event.
        record_audit(user=self.user, action=AuditLog.Action.LOGIN, obj=self.user)
        data["user"] = UserSerializer(self.user).data
        return data


class ClinicRegistrationSerializer(serializers.Serializer):
    clinic_name = serializers.CharField(max_length=255)
    clinic_email = serializers.EmailField(required=False, allow_blank=True, default="")
    clinic_phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, validators=[validate_password])

    # No validate_username here: this always creates a brand-new clinic, whose username
    # namespace (unique per clinic, see User.Meta.constraints) is guaranteed empty.

    def validate_clinic_name(self, value):
        # Case-insensitive, whitespace-trimmed uniqueness (Clinic.Meta.constraints) — the public
        # clinic picker on the patient portal shows name only (ClinicPublicSerializer), so a
        # duplicate name would be indistinguishable to a patient trying to pick their own clinic.
        value = value.strip()
        if Clinic.objects.filter(name__iexact=value).exists():
            raise serializers.ValidationError("A clinic with that name already exists.")
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value


class PatientActivationRequestSerializer(serializers.Serializer):
    clinic = serializers.IntegerField()
    patient_number = serializers.CharField(max_length=30)
    phone = serializers.CharField(max_length=32)
    date_of_birth = serializers.DateField()


class StaffListSerializer(serializers.ModelSerializer):
    """Read shape for the staff management screen. Unlike UserSerializer.roles (plural, used by
    /me/), role is a single value — a staff member managed through this screen has exactly one of
    STAFF_ROLES_ASSIGNABLE (or "doctor", surfaced read-only for completeness of the directory)."""

    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "role", "is_active", "date_joined"]

    def get_role(self, obj):
        return obj.groups.values_list("name", flat=True).first()


class StaffCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, validators=[validate_password])
    role = serializers.ChoiceField(choices=STAFF_ROLES_ASSIGNABLE)

    def validate_username(self, value):
        # Scoped per clinic (User.Meta.constraints) — the same username may already be taken by
        # an unrelated clinic, that's fine, only a collision within this clinic matters.
        clinic = self.context["request"].user.clinic
        if User.objects.filter(username=value, clinic=clinic).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate_email(self, value):
        # email is the global login identifier (User.USERNAME_FIELD) — unique across all clinics.
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value


class StaffUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]


class StaffRoleChangeSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=STAFF_ROLES_ASSIGNABLE)


class PatientActivationVerifySerializer(serializers.Serializer):
    clinic = serializers.IntegerField()
    patient_number = serializers.CharField(max_length=30)
    phone = serializers.CharField(max_length=32)
    date_of_birth = serializers.DateField()
    code = serializers.CharField(max_length=6)
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, validators=[validate_password])

    def validate_username(self, value):
        # Scoped per clinic (User.Meta.constraints). self.initial_data (raw payload) is used
        # rather than attrs, since field-level validators run before cross-field attrs exist.
        clinic_id = self.initial_data.get("clinic")
        if User.objects.filter(username=value, clinic_id=clinic_id).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value
