"""Alerte de seuil de stock — même schéma que appointments/notifications.py : construit
sujet/corps, appelle communication.services.send_notification() une fois par destinataire x
canal. business/notification-rules.md doit être mis à jour avec cet événement (STOCK BAS /
SURSTOCK) ; en attendant cette décision produit, les destinataires retenus ici sont le pharmacien
et l'administrateur de clinique de l'établissement concerné, sur le canal In-App uniquement (pas
d'email/SMS pour l'instant — à revoir avec la table faisant autorité)."""

from communication.models import NotificationLog
from communication.services import PLATFORM_SIGNATURE, compose_email, send_notification


def _stock_recipients(clinic):
    users = clinic.users.filter(groups__name__in=["pharmacist", "clinic_admin"], is_active=True).distinct()
    return [{"user": user, "email": user.email} for user in users if user.email]


def _dispatch(*, clinic, notification_type, subject, body):
    for entry in _stock_recipients(clinic):
        send_notification(
            clinic=clinic,
            recipient_user=entry["user"],
            channel=NotificationLog.Channel.EMAIL,
            notification_type=notification_type,
            recipient_address=entry["email"],
            subject=subject,
            body=compose_email(
                paragraphs=[body, "Vous pouvez consulter le stock depuis l'écran « Pharmacie »."],
                signature=PLATFORM_SIGNATURE,
            ),
        )


def send_low_stock_alert(*, medication_id):
    from .models import Medication

    try:
        medication = Medication.objects.select_related("clinic").get(pk=medication_id)
    except Medication.DoesNotExist:
        return
    subject = f"Alerte stock bas : {medication.name} — {medication.clinic.name}"
    body = (
        f"Le stock de {medication.name} est de {medication.current_stock} {medication.unit}, en dessous "
        f"du seuil minimal fixé à {medication.min_threshold} {medication.unit}. Nous vous recommandons "
        f"de prévoir un réapprovisionnement."
    )
    _dispatch(
        clinic=medication.clinic,
        notification_type=NotificationLog.NotificationType.STOCK_LOW,
        subject=subject,
        body=body,
    )


def send_overstock_alert(*, medication_id):
    from .models import Medication

    try:
        medication = Medication.objects.select_related("clinic").get(pk=medication_id)
    except Medication.DoesNotExist:
        return
    subject = f"Alerte surstock : {medication.name} — {medication.clinic.name}"
    body = (
        f"Le stock de {medication.name} est de {medication.current_stock} {medication.unit}, au-dessus "
        f"du seuil maximal fixé à {medication.max_threshold} {medication.unit}. Nous vous recommandons "
        f"de suspendre les commandes de ce médicament."
    )
    _dispatch(
        clinic=medication.clinic,
        notification_type=NotificationLog.NotificationType.STOCK_OVERSTOCK,
        subject=subject,
        body=body,
    )
