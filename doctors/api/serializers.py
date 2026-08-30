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
            raise serializers.ValidationError("Department does not belong to this clinic.")
        return department

    def create(self, validated_data):
        user_fields = ["username", "email", "first_name", "last_name", "password"]
        user_data = {field: validated_data.pop(field) for field in user_fields if field in validated_data}
        clinic = validated_data.pop("clinic")
        return create_doctor(clinic=clinic, user_data=user_data, **validated_data)
