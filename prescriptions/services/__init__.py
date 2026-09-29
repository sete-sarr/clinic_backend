from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from ..models import Prescription, PrescriptionItem

LOCKED_STATUSES = {Prescription.Status.VALIDATED, Prescription.Status.CANCELLED}


@transaction.atomic
def create_prescription(*, clinic, consultation, patient, doctor=None, items, **fields):
    if doctor is None:
        raise ValidationError(_("Le médecin est obligatoire."))
    if patient.clinic_id != clinic.id:
        # Message volontairement générique (audit de sécurité, 2026-09-02) : ne confirme pas si
        # l'ID soumis existe dans une autre clinique, afin d'éviter un oracle d'existence cross-tenant.
        raise ValidationError(_("Patient invalide."))
    if doctor.clinic_id != clinic.id:
        raise ValidationError(_("Médecin invalide."))
    if consultation.clinic_id != clinic.id:
        raise ValidationError(_("Consultation invalide."))
    if not items:
        raise ValidationError(_("Une ordonnance doit contenir au moins une ligne de médicament."))
    if hasattr(consultation, "prescription"):
        raise ValidationError(_("Cette consultation a déjà une ordonnance."))

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
        raise ValidationError(_("Une ordonnance validée ou annulée ne peut plus être modifiée."))

    if "patient" in fields and fields["patient"].clinic_id != prescription.clinic_id:
        # Message volontairement générique (audit de sécurité, 2026-09-02) : ne confirme pas si
        # l'ID soumis existe dans une autre clinique, afin d'éviter un oracle d'existence cross-tenant.
        raise ValidationError(_("Patient invalide."))
    if "doctor" in fields and fields["doctor"] and fields["doctor"].clinic_id != prescription.clinic_id:
        raise ValidationError(_("Médecin invalide."))

    for key, value in fields.items():
        setattr(prescription, key, value)
    prescription.save()

    if items is not None:
        if not items:
            raise ValidationError(_("Une ordonnance doit contenir au moins une ligne de médicament."))
        prescription.items.all().delete()
        PrescriptionItem.objects.bulk_create(
            [PrescriptionItem(prescription=prescription, **item) for item in items]
        )

    return prescription
