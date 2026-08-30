"""Builds and dispatches appointment created/cancelled notifications, following the same shape
as accounts/services.py's OTP notifications: build subject/body, call communication.services
.send_notification() once per recipient x channel. send_notification() itself already owns the
async boundary (it persists a NotificationLog synchronously, then defers actual provider delivery
via a Celery task) — so these are plain functions, not Celery tasks themselves, called from
appointments/services.py inside transaction.on_commit(...) once the appointment row is committed."""

from communication.models import NotificationLog
from communication.services import send_notification


def _patient_and_doctor(appointment):
    doctor_user = appointment.doctor.user
    return [
        {
            "user": getattr(appointment.patient, "user", None),
            "email": appointment.patient.email,
            "phone": appointment.patient.phone,
        },
        {"user": doctor_user, "email": doctor_user.email, "phone": ""},
    ]


def _recipients(appointment):
    """Patient, doctor, and every secretary-role user in the clinic (business/notification-rules.md's
    APPOINTMENT CREATED/CANCELLED rows list "Patient, Doctor, Receptionist" — there's no existing
    role->users resolution precedent elsewhere in the codebase for "the receptionist", so this
    resolves to all secretary-role users of the appointment's clinic)."""
    entries = _patient_and_doctor(appointment)
    for secretary in appointment.clinic.users.filter(groups__name="secretary"):
        entries.append({"user": secretary, "email": secretary.email, "phone": ""})
    return entries


def _dispatch(*, appointment, notification_type, subject, body, recipients=None):
    for entry in recipients if recipients is not None else _recipients(appointment):
        if entry["email"]:
            send_notification(
                clinic=appointment.clinic,
                recipient_user=entry["user"],
                channel=NotificationLog.Channel.EMAIL,
                notification_type=notification_type,
                recipient_address=entry["email"],
                subject=subject,
                body=body,
            )
        if entry["phone"]:
            send_notification(
                clinic=appointment.clinic,
                recipient_user=entry["user"],
                channel=NotificationLog.Channel.SMS,
                notification_type=notification_type,
                recipient_address=entry["phone"],
                subject=subject,
                body=body,
            )


def send_appointment_created_notifications(*, appointment_id):
    from .models import Appointment

    try:
        appointment = Appointment.objects.select_related("patient", "doctor__user", "clinic").get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return
    subject = f"Appointment scheduled - {appointment.clinic.name}"
    body = (
        f"Appointment with Dr. {appointment.doctor.user.get_full_name()} on "
        f"{appointment.date} at {appointment.time} has been scheduled."
    )
    _dispatch(
        appointment=appointment,
        notification_type=NotificationLog.NotificationType.APPOINTMENT_CREATED,
        subject=subject,
        body=body,
    )


def send_appointment_modified_notifications(*, appointment_id):
    from .models import Appointment

    try:
        appointment = Appointment.objects.select_related("patient", "doctor__user", "clinic").get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return
    subject = f"Appointment updated - {appointment.clinic.name}"
    body = (
        f"Your appointment with Dr. {appointment.doctor.user.get_full_name()} has been updated. "
        f"New date/time: {appointment.date} at {appointment.time}."
    )
    _dispatch(
        appointment=appointment,
        notification_type=NotificationLog.NotificationType.APPOINTMENT_MODIFIED,
        subject=subject,
        body=body,
    )


def send_appointment_cancelled_notifications(*, appointment_id):
    from .models import Appointment

    try:
        appointment = Appointment.objects.select_related("patient", "doctor__user", "clinic").get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return
    subject = f"Appointment cancelled - {appointment.clinic.name}"
    body = (
        f"Appointment with Dr. {appointment.doctor.user.get_full_name()} on "
        f"{appointment.date} at {appointment.time} has been cancelled."
    )
    _dispatch(
        appointment=appointment,
        notification_type=NotificationLog.NotificationType.APPOINTMENT_CANCELLED,
        subject=subject,
        body=body,
    )


def send_appointment_reminder_notifications(*, appointment_id):
    """24h-before reminder (business/notification-rules.md RAPPEL DE RENDEZ-VOUS: Patient, Médecin —
    no Receptionist here, unlike created/cancelled). Called from appointments/tasks.py's
    send_day_before_reminders, itself scheduled via django-celery-beat
    (appointments/migrations/0002_schedule_day_before_reminder.py)."""
    from .models import Appointment

    try:
        appointment = Appointment.objects.select_related("patient", "doctor__user", "clinic").get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return
    subject = f"Reminder: appointment tomorrow - {appointment.clinic.name}"
    body = (
        f"Reminder: appointment with Dr. {appointment.doctor.user.get_full_name()} on "
        f"{appointment.date} at {appointment.time}."
    )
    _dispatch(
        appointment=appointment,
        notification_type=NotificationLog.NotificationType.APPOINTMENT_REMINDER,
        subject=subject,
        body=body,
        recipients=_patient_and_doctor(appointment),
    )
