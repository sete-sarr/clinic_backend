from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from accounts.models import User
from doctors.models import Doctor
from doctors.services import create_doctor


class DoctorUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name"]
        read_only_fields = ["id"]


class DoctorSerializer(serializers.ModelSerializer):
    user = DoctorUserSerializer(read_only=True)
    username = serializers.CharField(write_only=True, required=False)
    email = serializers.EmailField(write_only=True, required=False)
    first_name = serializers.CharField(write_only=True, required=False)
    last_name = serializers.CharField(write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False, validators=[validate_password])

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

    def validate(self, attrs):
        # username/email are declared required=False so the existing PUT-based edit flow (which
        # never sends them — see doctor-form.ts) keeps working, but both are mandatory when
        # actually creating the underlying User account.
        if self.instance is None:
            for field in ("username", "email"):
                if not attrs.get(field):
                    raise serializers.ValidationError({field: "This field is required."})
        return attrs

    class Meta:
        model = Doctor
        fields = [
            "id",
            "user",
            "clinic",
            "department",
            "professional_number",
            "specialty",
            "phone",
            "is_active",
            "created_at",
            "updated_at",
            "username",
            "email",
            "first_name",
            "last_name",
            "password",
        ]
        read_only_fields = ["id", "clinic", "is_active", "created_at", "updated_at"]

    def validate_department(self, department):
        clinic = self.context["request"].user.clinic
        if department and department.clinic_id != clinic.id:
            # Deliberately generic (security audit, 2026-09-02): does not confirm whether the
            # submitted ID exists in another clinic, to avoid a cross-tenant existence oracle.
            raise serializers.ValidationError("Invalid department.")
        return department

    def create(self, validated_data):
        user_fields = ["username", "email", "first_name", "last_name", "password"]
        user_data = {field: validated_data.pop(field) for field in user_fields if field in validated_data}
        clinic = validated_data.pop("clinic")
        return create_doctor(clinic=clinic, user_data=user_data, **validated_data)
