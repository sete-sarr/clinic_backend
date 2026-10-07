"""Notifications du laboratoire (business/notification-rules.md), toutes via communication.services :

- DEMANDE DE LABORATOIRE CRÉÉE → techniciens de laboratoire, in-app, priorité haute ;
- RÉSULTAT HORS NORME → médecin prescripteur, in-app, priorité critique ;
- résultats prêts à valider → médecin prescripteur, in-app, priorité haute ;
- RÉSULTAT DISPONIBLE → patient, e-mail + SMS, après validation uniquement, sans jamais la valeur.

Le nom du patient figure dans les notifications in-app (destinées au personnel autorisé), jamais
une valeur de résultat : le détail s'ouvre par le lien, avec les contrôles d'accès habituels."""

from django.db import transaction
from django.utils import translation
from django.utils.translation import gettext as _

from communication.models import InAppNotification, NotificationLog
from communication.services import compose_email, language_for_clinic, notify_in_app, send_notification
from subscriptions.catalog import APPOINTMENT_SMS, has_feature


def _patient_name(order):
    return f"{order.patient.first_name} {order.patient.last_name}".strip()


def _link(order):
    return f"/laboratory/orders/{order.pk}"


def notify_order_created(order):
    technicians = order.clinic.users.filter(groups__name="lab_technician", is_active=True).distinct()
    count = order.items.count()

    def compose():
        return (
            _("Nouvelle demande d'examens : %(patient)s") % {"patient": _patient_name(order)},
            _("%(number)s — %(count)s examen(s) à prélever.") % {"number": order.number, "count": count},
        )

    notify_in_app(
        clinic=order.clinic, recipients=technicians, category=InAppNotification.Category.LABORATORY,
        priority=InAppNotification.Priority.HIGH, compose=compose, link=_link(order),
    )


def notify_abnormal_results(order, items):
    names = ", ".join(item.test_name for item in items)

    def compose():
        return (
            _("Résultat hors norme : %(patient)s") % {"patient": _patient_name(order)},
            _("%(number)s — %(tests)s.") % {"number": order.number, "tests": names},
        )

    notify_in_app(
        clinic=order.clinic, recipients=[order.doctor.user], category=InAppNotification.Category.LABORATORY,
        priority=InAppNotification.Priority.CRITICAL, compose=compose, link=_link(order),
    )


def notify_results_ready(order):
    def compose():
        return (
            _("Résultats à valider : %(patient)s") % {"patient": _patient_name(order)},
            _("%(number)s — tous les résultats ont été saisis.") % {"number": order.number},
        )

    notify_in_app(
        clinic=order.clinic, recipients=[order.doctor.user], category=InAppNotification.Category.LABORATORY,
        priority=InAppNotification.Priority.HIGH, compose=compose, link=_link(order),
    )


def notify_patient_results_available(order):
    """Après le commit de la validation : le patient n'est jamais prévenu d'un résultat non validé."""
    transaction.on_commit(lambda: _send_patient_results_available(order))


def _send_patient_results_available(order):
    patient = order.patient
    clinic = order.clinic
    if not (patient.email or patient.phone):
        return
    with translation.override(language_for_clinic(clinic)):
        subject = _("Vos résultats d'examens sont disponibles — %(clinic)s") % {"clinic": clinic.name}
        email_body = compose_email(
            paragraphs=[
                _("Les résultats de vos examens de laboratoire (%(number)s) ont été validés par votre médecin.")
                % {"number": order.number},
                _("Vous pouvez les consulter depuis votre espace patient, rubrique « Mes résultats »."),
            ],
            signature=clinic.name,
        )
        sms_body = _("%(clinic)s : vos résultats d'examens sont disponibles dans votre espace patient.") % {
            "clinic": clinic.name
        }
    common = {
        "clinic": clinic,
        "recipient_user": getattr(patient, "user", None),
        "notification_type": NotificationLog.NotificationType.LAB_RESULT_AVAILABLE,
        "subject": subject,
    }
    if patient.email:
        send_notification(**common, channel=NotificationLog.Channel.EMAIL, recipient_address=patient.email, body=email_body)
    # Les SMS aux patients relèvent de la même option d'abonnement que ceux des rendez-vous.
    if patient.phone and has_feature(clinic, APPOINTMENT_SMS):
        send_notification(**common, channel=NotificationLog.Channel.SMS, recipient_address=patient.phone, body=sms_body)
