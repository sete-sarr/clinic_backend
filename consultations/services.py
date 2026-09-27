from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Consultation

LOCKED_STATUSES = {Consultation.Status.VALIDATED}


def _check_single_consultation_per_appointment(*, appointment, is_follow_up, exclude_pk=None):
    if not appointment or is_follow_up:
        return
    qs = Consultation.objects.filter(appointment=appointment)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise ValidationError("Ce rendez-vous a déjà une consultation. Enregistrez la nouvelle comme consultation de suivi.")


@transaction.atomic
def create_consultation(*, clinic, patient, appointment=None, is_follow_up=False, doctor=None, **fields):
    if doctor is None:
        raise ValidationError("Le médecin est obligatoire.")
    if patient.clinic_id != clinic.id:
        # Délibérément générique (audit de sécurité, 2026-09-02) : ne confirme pas si l'ID soumis
        # existe dans une autre clinique, afin d'éviter un oracle d'existence inter-tenant.
        raise ValidationError("Patient invalide.")
    if doctor.clinic_id != clinic.id:
        raise ValidationError("Médecin invalide.")
    if appointment and appointment.clinic_id != clinic.id:
        raise ValidationError("Rendez-vous invalide.")
    _check_single_consultation_per_appointment(appointment=appointment, is_follow_up=is_follow_up)
    return Consultation.objects.create(
        clinic=clinic, patient=patient, appointment=appointment, is_follow_up=is_follow_up, doctor=doctor, **fields
    )


@transaction.atomic
def update_consultation(*, consultation, **fields):
    if consultation.status in LOCKED_STATUSES:
        raise ValidationError("Une consultation validée est en lecture seule et ne peut plus être modifiée.")

    if "patient" in fields and fields["patient"].clinic_id != consultation.clinic_id:
        # Délibérément générique (audit de sécurité, 2026-09-02) : ne confirme pas si l'ID soumis
        # existe dans une autre clinique, afin d'éviter un oracle d'existence inter-tenant.
        raise ValidationError("Patient invalide.")
    if "doctor" in fields and fields["doctor"] and fields["doctor"].clinic_id != consultation.clinic_id:
        raise ValidationError("Médecin invalide.")
    if "appointment" in fields and fields["appointment"] and fields["appointment"].clinic_id != consultation.clinic_id:
        raise ValidationError("Rendez-vous invalide.")

    if fields.get("status") == Consultation.Status.COMPLETED or (
        consultation.status == Consultation.Status.COMPLETED and "status" not in fields
    ):
        diagnosis = fields.get("diagnosis", consultation.diagnosis)
        treatment_plan = fields.get("treatment_plan", consultation.treatment_plan)
        chief_complaint = fields.get("chief_complaint", consultation.chief_complaint)
        if not (diagnosis and treatment_plan and chief_complaint):
            raise ValidationError(
                "Le motif de consultation, le diagnostic et le plan de traitement sont obligatoires pour terminer une consultation."
            )

    for key, value in fields.items():
        setattr(consultation, key, value)
    consultation.save()
    return consultation
