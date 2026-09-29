from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy

from common.models import SequenceCounter

from .models import Appointment

ACTIVE_STATUSES = [Appointment.Status.PENDING, Appointment.Status.CONFIRMED]
TERMINAL_STATUSES = {Appointment.Status.CANCELLED, Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW}
PATIENT_CANCELLABLE_STATUSES = [Appointment.Status.PENDING, Appointment.Status.CONFIRMED]

# Libellés français pour les messages d'erreur — les labels de Appointment.Status restent en anglais
# (les changer imposerait une migration) ; même vocabulaire que APPOINTMENT_STATUS_LABELS côté frontend.
_STATUS_LABELS_FR = {
    Appointment.Status.PENDING: gettext_lazy("en attente"),
    Appointment.Status.CONFIRMED: gettext_lazy("confirmé"),
    Appointment.Status.COMPLETED: gettext_lazy("terminé"),
    Appointment.Status.CANCELLED: gettext_lazy("annulé"),
    Appointment.Status.NO_SHOW: gettext_lazy("marqué absent"),
}


def _status_label(appointment):
    return _STATUS_LABELS_FR.get(appointment.status, appointment.status)


def validate_not_past(*, date, time):
    now = timezone.localtime(timezone.now())
    if date < now.date() or (date == now.date() and time < now.time()):
        raise ValidationError(_("Impossible de programmer un rendez-vous dans le passé."))


def validate_no_overlap(*, clinic, doctor, patient, date, time, exclude_pk=None):
    conflicts = Appointment.objects.filter(
        clinic=clinic, date=date, time=time, status__in=ACTIVE_STATUSES
    ).filter(models.Q(doctor=doctor) | models.Q(patient=patient))
    if exclude_pk:
        conflicts = conflicts.exclude(pk=exclude_pk)
    if conflicts.exists():
        raise ValidationError(_("Ce médecin ou ce patient a déjà un rendez-vous à cette date et à cette heure."))


@transaction.atomic
def create_appointment(*, clinic, doctor, patient, date, time, **fields):
    if patient.clinic_id != clinic.id:
        # Volontairement générique (audit de sécurité, 2026-09-02) : ne confirme pas si l'ID
        # soumis existe dans une autre clinique, afin d'éviter un oracle d'existence inter-tenant.
        raise ValidationError(_("Patient invalide."))
    if doctor.clinic_id != clinic.id:
        raise ValidationError(_("Médecin invalide."))
    # business/validation-rules.md VALIDATION DÉPARTEMENT : "Les rendez-vous requièrent un
    # département actif" / "Les départements inactifs ne peuvent pas recevoir de nouveaux
    # rendez-vous". Un médecin sans département assigné reste sans restriction (le département
    # est aujourd'hui optionnel sur Doctor).
    department = doctor.department
    if department is not None and department.status != department.Status.ACTIVE:
        raise ValidationError(_("Le département de ce médecin n'est pas actif et ne peut pas recevoir de nouveaux rendez-vous."))
    validate_not_past(date=date, time=time)
    validate_no_overlap(clinic=clinic, doctor=doctor, patient=patient, date=date, time=time)
    appointment = Appointment.objects.create(
        clinic=clinic, doctor=doctor, patient=patient, date=date, time=time, **fields
    )
    from .notifications import send_appointment_created_notifications

    transaction.on_commit(lambda: send_appointment_created_notifications(appointment_id=appointment.id))
    return appointment


@transaction.atomic
def update_appointment(*, appointment, **fields):
    if appointment.status in TERMINAL_STATUSES:
        raise ValidationError(_("Impossible de modifier ce rendez-vous : il est déjà %(status)s.") % {"status": _status_label(appointment)})

    date = fields.get("date", appointment.date)
    time = fields.get("time", appointment.time)
    doctor = fields.get("doctor", appointment.doctor)
    patient = fields.get("patient", appointment.patient)

    if "patient" in fields and patient.clinic_id != appointment.clinic_id:
        # Volontairement générique (audit de sécurité, 2026-09-02) : ne confirme pas si l'ID
        # soumis existe dans une autre clinique, afin d'éviter un oracle d'existence inter-tenant.
        raise ValidationError(_("Patient invalide."))
    if "doctor" in fields and doctor.clinic_id != appointment.clinic_id:
        raise ValidationError(_("Médecin invalide."))

    if "date" in fields or "time" in fields:
        validate_not_past(date=date, time=time)
    if any(key in fields for key in ("date", "time", "doctor", "patient")):
        validate_no_overlap(
            clinic=appointment.clinic,
            doctor=doctor,
            patient=patient,
            date=date,
            time=time,
            exclude_pk=appointment.pk,
        )

    for key, value in fields.items():
        setattr(appointment, key, value)
    appointment.save()

    if "date" in fields or "time" in fields:
        from .notifications import send_appointment_modified_notifications

        transaction.on_commit(lambda: send_appointment_modified_notifications(appointment_id=appointment.id))

    if fields.get("status") == Appointment.Status.CANCELLED:
        from .notifications import send_appointment_cancelled_notifications

        transaction.on_commit(lambda: send_appointment_cancelled_notifications(appointment_id=appointment.id))

    return appointment


def validate_cancellable_by_patient(*, appointment):
    if appointment.status not in PATIENT_CANCELLABLE_STATUSES:
        raise ValidationError(_("Impossible d'annuler ce rendez-vous : il est déjà %(status)s.") % {"status": _status_label(appointment)})
    appointment_dt = timezone.make_aware(datetime.combine(appointment.date, appointment.time))
    deadline = timezone.now() + timedelta(hours=settings.APPOINTMENT_CANCELLATION_DEADLINE_HOURS)
    if appointment_dt <= deadline:
        raise ValidationError(
            _("Un rendez-vous ne peut être annulé que plus de %(hours)s h à l'avance.")
            % {"hours": settings.APPOINTMENT_CANCELLATION_DEADLINE_HOURS}
        )


@transaction.atomic
def cancel_appointment_by_patient(*, appointment):
    """Transition dédiée et volontairement restreinte pour l'action d'auto-annulation du patient —
    délibérément non routée via update_appointment, qui reste sans restriction de transition pour
    les appelants staff."""
    validate_cancellable_by_patient(appointment=appointment)
    appointment.status = Appointment.Status.CANCELLED
    appointment.save(update_fields=["status", "updated_at"])

    from .notifications import send_appointment_cancelled_notifications

    transaction.on_commit(lambda: send_appointment_cancelled_notifications(appointment_id=appointment.id))
    return appointment


@transaction.atomic
def check_in_appointment(*, appointment):
    """Enregistrement à l'accueil : le patient est arrivé pour un rendez-vous du jour même. Génère
    un ticket_number par clinique/par année via le même mécanisme SequenceCounter déjà utilisé
    pour Patient.patient_number (patients/services/__init__.py::generate_patient_number) — pas un
    nouveau concept de compteur."""
    if appointment.checked_in_at is not None:
        raise ValidationError(_("L'arrivée du patient a déjà été enregistrée pour ce rendez-vous."))
    if appointment.status not in ACTIVE_STATUSES:
        raise ValidationError(
            _("Impossible d'enregistrer l'arrivée : ce rendez-vous est déjà %(status)s.") % {"status": _status_label(appointment)}
        )
    if appointment.date != timezone.localdate():
        raise ValidationError(_("L'arrivée ne peut être enregistrée que pour un rendez-vous prévu aujourd'hui."))

    year = timezone.localdate().year
    sequence = SequenceCounter.next_value(clinic=appointment.clinic, key="checkin_ticket", year=year)
    appointment.checked_in_at = timezone.now()
    appointment.ticket_number = f"CHK-{year}-{sequence:05d}"
    appointment.save(update_fields=["checked_in_at", "ticket_number", "updated_at"])
    return appointment
