"""Builds and dispatches appointment created/modified/cancelled/reminder notifications: build the
messages, call communication.services.send_notification() once per recipient x channel.
send_notification() itself already owns the async boundary (it persists a NotificationLog
synchronously, then defers actual provider delivery via a Celery task) — so these are plain
functions, not Celery tasks themselves, called from appointments/services.py inside
transaction.on_commit(...) once the appointment row is committed.

Deux rédactions par événement : l'une adressée au patient (« votre rendez-vous »), l'autre au
personnel (médecin, secrétariat), qui nomme le patient — un même texte ne peut pas convenir aux
deux (le médecin recevrait « votre rendez-vous avec le Dr [lui-même] »).

Langue (docs/i18n.md §2) : le patient reçoit ses messages dans la langue de la clinique, chaque
membre du personnel dans la sienne — jamais dans la langue de la personne qui a déclenché l'envoi.
Chaque version est composée dans `translation.override(<langue du destinataire>)`."""

from dataclasses import dataclass

from django.utils import translation
from django.utils.translation import gettext as _

from communication.models import NotificationLog
from communication.services import (
    compose_email,
    format_message_date,
    format_message_time,
    language_for_clinic,
    language_for_user,
    send_notification,
)
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
    """« le 01/10/2026 à 09h30 » / « on October 1, 2026 at 9:30 AM » selon la langue active."""
    return _("le %(date)s à %(time)s") % {
        "date": format_message_date(appointment.date),
        "time": format_message_time(appointment.time),
    }


def _doctor_name(appointment):
    user = appointment.doctor.user
    return _("Dr %(name)s") % {"name": user.get_full_name() or user.get_username()}


def _patient_name(appointment):
    return f"{appointment.patient.first_name} {appointment.patient.last_name}".strip()


def _contact(appointment):
    """« contacter la clinique au 33 800 00 00 » quand la clinique a renseigné un téléphone."""
    phone = appointment.clinic.phone
    if phone:
        return _("contacter la clinique au %(phone)s") % {"phone": phone}
    return _("contacter la clinique")


def _patient_message(appointment, *, subject, paragraphs, sms):
    clinic = appointment.clinic.name
    return _Message(
        subject=f"{subject} — {clinic}",
        email_body=compose_email(paragraphs=paragraphs, signature=clinic),
        sms_body=_("%(clinic)s : %(message)s") % {"clinic": clinic, "message": sms},
    )


def _staff_message(appointment, *, subject, paragraphs):
    return _Message(
        subject=f"{subject} — {_patient_name(appointment)}",
        email_body=compose_email(paragraphs=paragraphs, signature=appointment.clinic.name),
    )


def _in_language(builder, language, cache):
    """Compose la version `language` du message une seule fois, quel que soit le nombre de
    destinataires qui la partagent."""
    if language not in cache:
        with translation.override(language):
            cache[language] = builder()
    return cache[language]


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


def _dispatch(*, appointment, notification_type, build_patient, build_staff, include_secretaries=True):
    """Patient (e-mail + SMS), médecin et — sauf pour le rappel — secrétariat (e-mail).
    business/notification-rules.md : RENDEZ-VOUS CRÉÉ/MODIFIÉ/ANNULÉ → Patient, Médecin,
    Réceptionniste (tous les utilisateurs du rôle secretary de la clinique) ; RAPPEL → Patient,
    Médecin. User n'a pas de champ téléphone : seul le patient peut recevoir un SMS."""
    patient = appointment.patient
    common = {"appointment": appointment, "notification_type": notification_type}
    patient_user = getattr(patient, "user", None)

    if patient.email or patient.phone:
        patient_message = _in_language(build_patient, language_for_clinic(appointment.clinic), {})
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
    staff_messages = {}
    for user in staff:
        if user.email:
            message = _in_language(build_staff, language_for_user(user), staff_messages)
            _send(**common, user=user, channel=NotificationLog.Channel.EMAIL, address=user.email,
                  message=message, body=message.email_body)


def send_appointment_created_notifications(*, appointment_id):
    appointment = _load(appointment_id)
    if not appointment:
        return

    def patient():
        doctor, when = _doctor_name(appointment), _when(appointment)
        return _patient_message(
            appointment,
            subject=_("Confirmation de votre rendez-vous"),
            paragraphs=[
                _("Nous vous confirmons l'enregistrement de votre rendez-vous avec le %(doctor)s %(when)s.")
                % {"doctor": doctor, "when": when},
                _("En cas d'empêchement, nous vous remercions de bien vouloir %(contact)s le plus tôt possible.")
                % {"contact": _contact(appointment)},
            ],
            sms=_("votre rendez-vous avec le %(doctor)s est confirmé %(when)s. En cas d'empêchement, merci de nous prévenir.")
            % {"doctor": doctor, "when": when},
        )

    def staff():
        return _staff_message(
            appointment,
            subject=_("Nouveau rendez-vous"),
            paragraphs=[
                _("Un rendez-vous a été enregistré pour %(patient)s avec le %(doctor)s %(when)s.")
                % {"patient": _patient_name(appointment), "doctor": _doctor_name(appointment), "when": _when(appointment)},
            ],
        )

    _dispatch(appointment=appointment, notification_type=NotificationLog.NotificationType.APPOINTMENT_CREATED,
              build_patient=patient, build_staff=staff)


def send_appointment_modified_notifications(*, appointment_id):
    appointment = _load(appointment_id)
    if not appointment:
        return

    def patient():
        doctor, when = _doctor_name(appointment), _when(appointment)
        return _patient_message(
            appointment,
            subject=_("Modification de votre rendez-vous"),
            paragraphs=[
                _("Nous vous informons que votre rendez-vous avec le %(doctor)s a été modifié. Il aura désormais lieu %(when)s.")
                % {"doctor": doctor, "when": when},
                _("Si cette nouvelle date ne vous convient pas, nous vous invitons à %(contact)s.")
                % {"contact": _contact(appointment)},
            ],
            sms=_("votre rendez-vous avec le %(doctor)s a été déplacé %(when)s. Pour toute question, merci de nous contacter.")
            % {"doctor": doctor, "when": when},
        )

    def staff():
        return _staff_message(
            appointment,
            subject=_("Rendez-vous modifié"),
            paragraphs=[
                _("Le rendez-vous de %(patient)s avec le %(doctor)s a été déplacé %(when)s.")
                % {"patient": _patient_name(appointment), "doctor": _doctor_name(appointment), "when": _when(appointment)},
            ],
        )

    _dispatch(appointment=appointment, notification_type=NotificationLog.NotificationType.APPOINTMENT_MODIFIED,
              build_patient=patient, build_staff=staff)


def send_appointment_cancelled_notifications(*, appointment_id):
    appointment = _load(appointment_id)
    if not appointment:
        return

    def patient():
        doctor, when = _doctor_name(appointment), _when(appointment)
        return _patient_message(
            appointment,
            subject=_("Annulation de votre rendez-vous"),
            paragraphs=[
                _("Nous vous informons que votre rendez-vous avec le %(doctor)s, prévu %(when)s, a été annulé.")
                % {"doctor": doctor, "when": when},
                _("Pour convenir d'un nouveau rendez-vous, nous vous invitons à %(contact)s.")
                % {"contact": _contact(appointment)},
            ],
            sms=_("votre rendez-vous avec le %(doctor)s prévu %(when)s est annulé. Contactez-nous pour en fixer un nouveau.")
            % {"doctor": doctor, "when": when},
        )

    def staff():
        return _staff_message(
            appointment,
            subject=_("Rendez-vous annulé"),
            paragraphs=[
                _("Le rendez-vous de %(patient)s avec le %(doctor)s, prévu %(when)s, a été annulé.")
                % {"patient": _patient_name(appointment), "doctor": _doctor_name(appointment), "when": _when(appointment)},
            ],
        )

    _dispatch(appointment=appointment, notification_type=NotificationLog.NotificationType.APPOINTMENT_CANCELLED,
              build_patient=patient, build_staff=staff)


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

    def patient():
        doctor, when = _doctor_name(appointment), _when(appointment)
        return _patient_message(
            appointment,
            subject=_("Rappel de votre rendez-vous de demain"),
            paragraphs=[
                _("Nous vous rappelons votre rendez-vous de demain avec le %(doctor)s, %(when)s.")
                % {"doctor": doctor, "when": when},
                _("Nous vous remercions de vous présenter quelques minutes en avance. En cas d'empêchement, merci de bien vouloir %(contact)s.")
                % {"contact": _contact(appointment)},
            ],
            sms=_("rappel de votre rendez-vous demain avec le %(doctor)s, %(when)s. En cas d'empêchement, merci de nous prévenir.")
            % {"doctor": doctor, "when": when},
        )

    def staff():
        return _staff_message(
            appointment,
            subject=_("Rappel : rendez-vous de demain"),
            paragraphs=[
                _("Pour rappel, vous recevez %(patient)s demain, %(when)s.")
                % {"patient": _patient_name(appointment), "when": _when(appointment)},
            ],
        )

    _dispatch(appointment=appointment, notification_type=NotificationLog.NotificationType.APPOINTMENT_REMINDER,
              build_patient=patient, build_staff=staff, include_secretaries=False)
