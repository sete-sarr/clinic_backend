from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from common.models import SequenceCounter

from ..models import Patient


def _check_duplicate(*, clinic, first_name, last_name, date_of_birth, phone):
    """business/validation-rules.md : détection des doublons requise à la création d'un patient."""
    duplicate = Patient.objects.filter(
        clinic=clinic,
        first_name__iexact=first_name,
        last_name__iexact=last_name,
        date_of_birth=date_of_birth,
    ).exclude(phone="").filter(phone=phone)
    if duplicate.exists():
        raise ValidationError(
            "A patient with the same name, date of birth, and phone number already exists in this clinic."
        )


def generate_patient_number(*, clinic, year=None):
    year = year or timezone.now().year
    sequence = SequenceCounter.next_value(clinic=clinic, key="patient_number", year=year)
    return f"PAT-{year}-{sequence:05d}"


@transaction.atomic
def create_patient(*, clinic, **fields):
    _check_duplicate(
        clinic=clinic,
        first_name=fields["first_name"],
        last_name=fields["last_name"],
        date_of_birth=fields["date_of_birth"],
        phone=fields.get("phone", ""),
    )
    patient = Patient.objects.create(
        clinic=clinic, patient_number=generate_patient_number(clinic=clinic), **fields
    )

    # Crée automatiquement le dossier médical vide (business/permissions-matrix.md : le dossier
    # médical existe dès le premier contact du patient avec la clinique).
    from medical_records.models import MedicalRecord

    MedicalRecord.objects.get_or_create(patient=patient, defaults={"clinic": clinic})

    return patient


def validate_date_of_birth(value: date):
    if value > timezone.now().date():
        raise ValidationError("Date of birth cannot be in the future.")
