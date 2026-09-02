from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from common.models import SequenceCounter

from .models import Appointment

ACTIVE_STATUSES = [Appointment.Status.PENDING, Appointment.Status.CONFIRMED]
TERMINAL_STATUSES = {Appointment.Status.CANCELLED, Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW}
PATIENT_CANCELLABLE_STATUSES = [Appointment.Status.PENDING, Appointment.Status.CONFIRMED]


def validate_not_past(*, date, time):
    now = timezone.localtime(timezone.now())
    if date < now.date() or (date == now.date() and time < now.time()):
        raise ValidationError("Cannot schedule an appointment in the past.")


def validate_no_overlap(*, clinic, doctor, patient, date, time, exclude_pk=None):
    conflicts = Appointment.objects.filter(
        clinic=clinic, date=date, time=time, status__in=ACTIVE_STATUSES
    ).filter(models.Q(doctor=doctor) | models.Q(patient=patient))
    if exclude_pk:
        conflicts = conflicts.exclude(pk=exclude_pk)
    if conflicts.exists():
        raise ValidationError("This doctor or patient already has an appointment at that date and time.")


@transaction.atomic
def create_appointment(*, clinic, doctor, patient, date, time, **fields):
    if patient.clinic_id != clinic.id:
        # Deliberately generic (security audit, 2026-09-02): does not confirm whether the
        # submitted ID exists in another clinic, to avoid a cross-tenant existence oracle.
        raise ValidationError("Invalid patient.")
    if doctor.clinic_id != clinic.id:
        raise ValidationError("Invalid doctor.")
    # business/validation-rules.md VALIDATION DÉPARTEMENT: "Les rendez-vous requièrent un
    # département actif" / "Les départements inactifs ne peuvent pas recevoir de nouveaux
    # rendez-vous". A doctor with no department assigned is left unrestricted (department is
    # optional on Doctor today).
    department = doctor.department
    if department is not None and department.status != department.Status.ACTIVE:
        raise ValidationError("This doctor's department is not active and cannot accept new appointments.")
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
        raise ValidationError(f"Cannot modify an appointment that is already {appointment.get_status_display()}.")

    date = fields.get("date", appointment.date)
    time = fields.get("time", appointment.time)
    doctor = fields.get("doctor", appointment.doctor)
    patient = fields.get("patient", appointment.patient)

    if "patient" in fields and patient.clinic_id != appointment.clinic_id:
        # Deliberately generic (security audit, 2026-09-02): does not confirm whether the
        # submitted ID exists in another clinic, to avoid a cross-tenant existence oracle.
        raise ValidationError("Invalid patient.")
    if "doctor" in fields and doctor.clinic_id != appointment.clinic_id:
        raise ValidationError("Invalid doctor.")

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
        raise ValidationError(f"Cannot cancel an appointment that is already {appointment.get_status_display()}.")
    appointment_dt = timezone.make_aware(datetime.combine(appointment.date, appointment.time))
    deadline = timezone.now() + timedelta(hours=settings.APPOINTMENT_CANCELLATION_DEADLINE_HOURS)
    if appointment_dt <= deadline:
        raise ValidationError(
            f"Appointments can only be cancelled more than "
            f"{settings.APPOINTMENT_CANCELLATION_DEADLINE_HOURS}h in advance."
        )


@transaction.atomic
def cancel_appointment_by_patient(*, appointment):
    """Dedicated, narrowly-scoped transition for the patient self-cancel action — deliberately not
    routed through update_appointment, which stays unrestricted-by-transition for staff callers."""
    validate_cancellable_by_patient(appointment=appointment)
    appointment.status = Appointment.Status.CANCELLED
    appointment.save(update_fields=["status", "updated_at"])

    from .notifications import send_appointment_cancelled_notifications

    transaction.on_commit(lambda: send_appointment_cancelled_notifications(appointment_id=appointment.id))
    return appointment


@transaction.atomic
def check_in_appointment(*, appointment):
    """Reception check-in: patient has arrived for a same-day appointment. Generates a
    per-clinic/per-year ticket_number via the same SequenceCounter mechanism already used for
    Patient.patient_number (patients/services/__init__.py::generate_patient_number) — not a new
    counter concept."""
    if appointment.checked_in_at is not None:
        raise ValidationError("This appointment has already been checked in.")
    if appointment.status not in ACTIVE_STATUSES:
        raise ValidationError(f"Cannot check in an appointment that is already {appointment.get_status_display()}.")
    if appointment.date != timezone.localdate():
        raise ValidationError("Can only check in an appointment scheduled for today.")

    year = timezone.localdate().year
    sequence = SequenceCounter.next_value(clinic=appointment.clinic, key="checkin_ticket", year=year)
    appointment.checked_in_at = timezone.now()
    appointment.ticket_number = f"CHK-{year}-{sequence:05d}"
    appointment.save(update_fields=["checked_in_at", "ticket_number", "updated_at"])
    return appointment
