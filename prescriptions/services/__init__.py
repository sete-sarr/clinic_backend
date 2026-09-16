from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import Prescription, PrescriptionItem

LOCKED_STATUSES = {Prescription.Status.VALIDATED, Prescription.Status.CANCELLED}


@transaction.atomic
def create_prescription(*, clinic, consultation, patient, doctor=None, items, **fields):
    if doctor is None:
        raise ValidationError("Doctor is required.")
    if patient.clinic_id != clinic.id:
        # Message volontairement générique (audit de sécurité, 2026-09-02) : ne confirme pas si
        # l'ID soumis existe dans une autre clinique, afin d'éviter un oracle d'existence cross-tenant.
        raise ValidationError("Invalid patient.")
    if doctor.clinic_id != clinic.id:
        raise ValidationError("Invalid doctor.")
    if consultation.clinic_id != clinic.id:
        raise ValidationError("Invalid consultation.")
    if not items:
        raise ValidationError("A prescription must contain at least one medication line.")
    if hasattr(consultation, "prescription"):
        raise ValidationError("This consultation already has a prescription.")

    prescription = Prescription.objects.create(
        clinic=clinic, consultation=consultation, patient=patient, doctor=doctor, **fields
    )
    PrescriptionItem.objects.bulk_create(
        [PrescriptionItem(prescription=prescription, **item) for item in items]
    )
    return prescription


@transaction.atomic
def update_prescription(*, prescription, items=None, **fields):
    if prescription.status in LOCKED_STATUSES:
        raise ValidationError("A validated or cancelled prescription can no longer be edited.")

    if "patient" in fields and fields["patient"].clinic_id != prescription.clinic_id:
        # Message volontairement générique (audit de sécurité, 2026-09-02) : ne confirme pas si
        # l'ID soumis existe dans une autre clinique, afin d'éviter un oracle d'existence cross-tenant.
        raise ValidationError("Invalid patient.")
    if "doctor" in fields and fields["doctor"] and fields["doctor"].clinic_id != prescription.clinic_id:
        raise ValidationError("Invalid doctor.")

    for key, value in fields.items():
        setattr(prescription, key, value)
    prescription.save()

    if items is not None:
        if not items:
            raise ValidationError("A prescription must contain at least one medication line.")
        prescription.items.all().delete()
        PrescriptionItem.objects.bulk_create(
            [PrescriptionItem(prescription=prescription, **item) for item in items]
        )

    return prescription
