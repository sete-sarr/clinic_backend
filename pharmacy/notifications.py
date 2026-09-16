"""Alerte de seuil de stock — même schéma que appointments/notifications.py : construit
sujet/corps, appelle communication.services.send_notification() une fois par destinataire x
canal. business/notification-rules.md doit être mis à jour avec cet événement (STOCK BAS /
SURSTOCK) ; en attendant cette décision produit, les destinataires retenus ici sont le pharmacien
et l'administrateur de clinique de l'établissement concerné, sur le canal In-App uniquement (pas
d'email/SMS pour l'instant — à revoir avec la table faisant autorité)."""

from communication.models import NotificationLog
from communication.services import send_notification


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
            body=body,
        )


def send_low_stock_alert(*, medication_id):
    from .models import Medication

    try:
        medication = Medication.objects.select_related("clinic").get(pk=medication_id)
    except Medication.DoesNotExist:
        return
    subject = f"Stock bas — {medication.name}"
    body = (
        f"Le stock de {medication.name} ({medication.current_stock} {medication.unit}) est passé "
        f"sous le seuil minimal ({medication.min_threshold} {medication.unit})."
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
    subject = f"Surstock — {medication.name}"
    body = (
        f"Le stock de {medication.name} ({medication.current_stock} {medication.unit}) dépasse le "
        f"seuil maximal ({medication.max_threshold} {medication.unit})."
    )
    _dispatch(
        clinic=medication.clinic,
        notification_type=NotificationLog.NotificationType.STOCK_OVERSTOCK,
        subject=subject,
        body=body,
    )
