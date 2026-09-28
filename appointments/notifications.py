"""Builds and dispatches appointment created/modified/cancelled/reminder notifications: build the
messages, call communication.services.send_notification() once per recipient x channel.
send_notification() itself already owns the async boundary (it persists a NotificationLog
synchronously, then defers actual provider delivery via a Celery task) — so these are plain
functions, not Celery tasks themselves, called from appointments/services.py inside
transaction.on_commit(...) once the appointment row is committed.

Deux rédactions par événement : l'une adressée au patient (« votre rendez-vous »), l'autre au
personnel (médecin, secrétariat), qui nomme le patient — un même texte ne peut pas convenir aux
deux (le médecin recevrait « votre rendez-vous avec le Dr [lui-même] »)."""

from dataclasses import dataclass

from communication.models import NotificationLog
from communication.services import compose_email, send_notification
from subscriptions.catalog import APPOINTMENT_REMINDERS, APPOINTMENT_SMS, has_feature


@dataclass(frozen=True)
class _Message:
    subject: str
    email_body: str
    sms_body: str = ""


def _load(appointment_id):
    from .models import Appointment

    return (
        Appointment.objects.select_related("patient", "doctor__user", "clinic")
        .filter(pk=appointment_id)
        .first()
    )


def _when(appointment):
    """« le 01/10/2026 à 09h30 » — format français lisible dans un e-mail comme dans un SMS."""
    return f"le {appointment.date:%d/%m/%Y} à {appointment.time:%Hh%M}"


def _doctor_name(appointment):
    user = appointment.doctor.user
    return f"Dr {user.get_full_name() or user.get_username()}"


def _patient_name(appointment):
    return f"{appointment.patient.first_name} {appointment.patient.last_name}".strip()


def _contact(appointment):
    """« contacter la clinique au 33 800 00 00 » quand la clinique a renseigné un téléphone."""
    phone = appointment.clinic.phone
    return f"contacter la clinique au {phone}" if phone else "contacter la clinique"


def _patient_message(appointment, *, subject, paragraphs, sms):
    clinic = appointment.clinic.name
    return _Message(
        subject=f"{subject} — {clinic}",
        email_body=compose_email(paragraphs=paragraphs, signature=clinic),
        sms_body=f"{clinic} : {sms}",
    )


def _staff_message(appointment, *, subject, paragraphs):
    return _Message(
        subject=f"{subject} — {_patient_name(appointment)}",
        email_body=compose_email(paragraphs=paragraphs, signature=appointment.clinic.name),
    )


def _send(*, appointment, notification_type, user, channel, address, message, body):
    send_notification(
        clinic=appointment.clinic,
        recipient_user=user,
        channel=channel,
        notification_type=notification_type,
        recipient_address=address,
        subject=message.subject,
        body=body,
    )


def _dispatch(*, appointment, notification_type, patient_message, staff_message, include_secretaries=True):
    """Patient (e-mail + SMS), médecin et — sauf pour le rappel — secrétariat (e-mail).
    business/notification-rules.md : RENDEZ-VOUS CRÉÉ/MODIFIÉ/ANNULÉ → Patient, Médecin,
    Réceptionniste (tous les utilisateurs du rôle secretary de la clinique) ; RAPPEL → Patient,
    Médecin. User n'a pas de champ téléphone : seul le patient peut recevoir un SMS."""
    patient = appointment.patient
    common = {"appointment": appointment, "notification_type": notification_type}
    patient_user = getattr(patient, "user", None)

    if patient.email:
        _send(**common, user=patient_user, channel=NotificationLog.Channel.EMAIL, address=patient.email,
              message=patient_message, body=patient_message.email_body)
    # SMS de rendez-vous réservés à la formule Professional (et à l'essai) — subscriptions/catalog.py.
    if patient.phone and has_feature(appointment.clinic, APPOINTMENT_SMS):
        _send(**common, user=patient_user, channel=NotificationLog.Channel.SMS, address=patient.phone,
              message=patient_message, body=patient_message.sms_body)

    staff = [appointment.doctor.user]
    if include_secretaries:
        staff += list(appointment.clinic.users.filter(groups__name="secretary"))
    for user in staff:
        if user.email:
            _send(**common, user=user, channel=NotificationLog.Channel.EMAIL, address=user.email,
                  message=staff_message, body=staff_message.email_body)


def send_appointment_created_notifications(*, appointment_id):
    appointment = _load(appointment_id)
    if not appointment:
        return
    doctor, when = _doctor_name(appointment), _when(appointment)
    _dispatch(
        appointment=appointment,
        notification_type=NotificationLog.NotificationType.APPOINTMENT_CREATED,
        patient_message=_patient_message(
            appointment,
            subject="Confirmation de votre rendez-vous",
            paragraphs=[
                f"Nous vous confirmons l'enregistrement de votre rendez-vous avec le {doctor} {when}.",
                f"En cas d'empêchement, nous vous remercions de bien vouloir {_contact(appointment)} "
                "le plus tôt possible.",
            ],
            sms=f"votre rendez-vous avec le {doctor} est confirmé {when}. En cas d'empêchement, "
                "merci de nous prévenir.",
        ),
        staff_message=_staff_message(
            appointment,
            subject="Nouveau rendez-vous",
            paragraphs=[f"Un rendez-vous a été enregistré pour {_patient_name(appointment)} avec le {doctor} {when}."],
        ),
    )


def send_appointment_modified_notifications(*, appointment_id):
    appointment = _load(appointment_id)
    if not appointment:
        return
    doctor, when = _doctor_name(appointment), _when(appointment)
    _dispatch(
        appointment=appointment,
        notification_type=NotificationLog.NotificationType.APPOINTMENT_MODIFIED,
        patient_message=_patient_message(
            appointment,
            subject="Modification de votre rendez-vous",
            paragraphs=[
                f"Nous vous informons que votre rendez-vous avec le {doctor} a été modifié. "
                f"Il aura désormais lieu {when}.",
                f"Si cette nouvelle date ne vous convient pas, nous vous invitons à {_contact(appointment)}.",
            ],
            sms=f"votre rendez-vous avec le {doctor} a été déplacé {when}. Pour toute question, "
                "merci de nous contacter.",
        ),
        staff_message=_staff_message(
            appointment,
            subject="Rendez-vous modifié",
            paragraphs=[f"Le rendez-vous de {_patient_name(appointment)} avec le {doctor} a été déplacé {when}."],
        ),
    )


def send_appointment_cancelled_notifications(*, appointment_id):
    appointment = _load(appointment_id)
    if not appointment:
        return
    doctor, when = _doctor_name(appointment), _when(appointment)
    _dispatch(
        appointment=appointment,
        notification_type=NotificationLog.NotificationType.APPOINTMENT_CANCELLED,
        patient_message=_patient_message(
            appointment,
            subject="Annulation de votre rendez-vous",
            paragraphs=[
                f"Nous vous informons que votre rendez-vous avec le {doctor}, prévu {when}, a été annulé.",
                f"Pour convenir d'un nouveau rendez-vous, nous vous invitons à {_contact(appointment)}.",
            ],
            sms=f"votre rendez-vous avec le {doctor} prévu {when} est annulé. Contactez-nous pour en "
                "fixer un nouveau.",
        ),
        staff_message=_staff_message(
            appointment,
            subject="Rendez-vous annulé",
            paragraphs=[
                f"Le rendez-vous de {_patient_name(appointment)} avec le {doctor}, prévu {when}, a été annulé."
            ],
        ),
    )


def send_appointment_reminder_notifications(*, appointment_id):
    """24h-before reminder (business/notification-rules.md RAPPEL DE RENDEZ-VOUS: Patient, Médecin —
    no Receptionist here, unlike created/cancelled). Called from appointments/tasks.py's
    send_day_before_reminders, itself scheduled via django-celery-beat
    (appointments/migrations/0002_schedule_day_before_reminder.py)."""
    appointment = _load(appointment_id)
    if not appointment:
        return
    if not has_feature(appointment.clinic, APPOINTMENT_REMINDERS):
        return  # rappel de la veille réservé à Professional (et à l'essai) — subscriptions/catalog.py
    doctor, when = _doctor_name(appointment), _when(appointment)
    _dispatch(
        appointment=appointment,
        notification_type=NotificationLog.NotificationType.APPOINTMENT_REMINDER,
        patient_message=_patient_message(
            appointment,
            subject="Rappel de votre rendez-vous de demain",
            paragraphs=[
                f"Nous vous rappelons votre rendez-vous de demain avec le {doctor}, {when}.",
                "Nous vous remercions de vous présenter quelques minutes en avance. En cas "
                f"d'empêchement, merci de bien vouloir {_contact(appointment)}.",
            ],
            sms=f"rappel de votre rendez-vous demain avec le {doctor}, {when}. En cas d'empêchement, "
                "merci de nous prévenir.",
        ),
        staff_message=_staff_message(
            appointment,
            subject="Rappel : rendez-vous de demain",
            paragraphs=[f"Pour rappel, vous recevez {_patient_name(appointment)} demain, {when}."],
        ),
        include_secretaries=False,
    )
