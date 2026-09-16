from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import User

from .models import Doctor


@transaction.atomic
def create_doctor(*, clinic, professional_number, specialty, user_data, department=None, phone=""):
    if department and department.clinic_id != clinic.id:
        # Délibérément générique (audit de sécurité, 2026-09-02) : ne confirme pas si l'ID soumis
        # existe dans une autre clinique, afin d'éviter un oracle d'existence inter-tenant.
        raise ValidationError("Invalid department.")
    user = User.objects.create_user(
        username=user_data["username"],
        email=user_data.get("email", ""),
        password=user_data["password"],
        first_name=user_data.get("first_name", ""),
        last_name=user_data.get("last_name", ""),
        clinic=clinic,
    )
    doctor_group, _ = Group.objects.get_or_create(name="doctor")
    user.groups.add(doctor_group)
    return Doctor.objects.create(
        user=user,
        clinic=clinic,
        department=department,
        professional_number=professional_number,
        specialty=specialty,
        phone=phone,
    )
