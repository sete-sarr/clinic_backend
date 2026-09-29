"""Alerte de seuil de stock — même schéma que appointments/notifications.py : construit
sujet/corps, appelle communication.services.send_notification() une fois par destinataire x
canal. business/notification-rules.md doit être mis à jour avec cet événement (STOCK BAS /
SURSTOCK) ; en attendant cette décision produit, les destinataires retenus ici sont le pharmacien
et l'administrateur de clinique de l'établissement concerné, sur le canal In-App uniquement (pas
d'email/SMS pour l'instant — à revoir avec la table faisant autorité)."""

from django.utils import translation
from django.utils.translation import gettext as _

from communication.models import NotificationLog
from communication.services import PLATFORM_SIGNATURE, compose_email, language_for_user, send_notification


def _stock_recipients(clinic):
    users = clinic.users.filter(groups__name__in=["pharmacist", "clinic_admin"], is_active=True).distinct()
    return [{"user": user, "email": user.email} for user in users if user.email]


def _dispatch(*, clinic, notification_type, build):
    """`build()` renvoie (sujet, paragraphe) ; il est appelé dans la langue de chaque destinataire
    (docs/i18n.md §2)."""
    for entry in _stock_recipients(clinic):
        with translation.override(language_for_user(entry["user"])):
            subject, body = build()
            email_body = compose_email(
                paragraphs=[body, _("Vous pouvez consulter le stock depuis l'écran « Pharmacie ».")],
                signature=PLATFORM_SIGNATURE,
            )
        send_notification(
            clinic=clinic,
            recipient_user=entry["user"],
            channel=NotificationLog.Channel.EMAIL,
            notification_type=notification_type,
            recipient_address=entry["email"],
            subject=subject,
            body=email_body,
        )


def send_low_stock_alert(*, medication_id):
    from .models import Medication

    try:
        medication = Medication.objects.select_related("clinic").get(pk=medication_id)
    except Medication.DoesNotExist:
        return
    values = {
        "name": medication.name, "clinic": medication.clinic.name, "stock": medication.current_stock,
        "unit": medication.unit, "threshold": medication.min_threshold,
    }

    def build():
        return (
            _("Alerte stock bas : %(name)s — %(clinic)s") % values,
            _(
                "Le stock de %(name)s est de %(stock)s %(unit)s, en dessous du seuil minimal fixé à "
                "%(threshold)s %(unit)s. Nous vous recommandons de prévoir un réapprovisionnement."
            ) % values,
        )

    _dispatch(clinic=medication.clinic, notification_type=NotificationLog.NotificationType.STOCK_LOW, build=build)


def send_overstock_alert(*, medication_id):
    from .models import Medication

    try:
        medication = Medication.objects.select_related("clinic").get(pk=medication_id)
    except Medication.DoesNotExist:
        return
    values = {
        "name": medication.name, "clinic": medication.clinic.name, "stock": medication.current_stock,
        "unit": medication.unit, "threshold": medication.max_threshold,
    }

    def build():
        return (
            _("Alerte surstock : %(name)s — %(clinic)s") % values,
            _(
                "Le stock de %(name)s est de %(stock)s %(unit)s, au-dessus du seuil maximal fixé à "
                "%(threshold)s %(unit)s. Nous vous recommandons de suspendre les commandes de ce médicament."
            ) % values,
        )

    _dispatch(clinic=medication.clinic, notification_type=NotificationLog.NotificationType.STOCK_OVERSTOCK, build=build)
