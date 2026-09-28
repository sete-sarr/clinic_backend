from celery import shared_task
from django.utils import timezone

from clinics.models import Clinic
from communication.models import NotificationLog
from communication.services import PLATFORM_SIGNATURE, compose_email, send_notification

# business/notification-rules.md "SUBSCRIPTION EXPIRING" : 30/15/7/1 jours avant current_period_end.
_THRESHOLDS = [
    (30, "expiring_notified_30d_at"),
    (15, "expiring_notified_15d_at"),
    (7, "expiring_notified_7d_at"),
    (1, "expiring_notified_1d_at"),
]


@shared_task
def send_subscription_expiring_notifications():
    """S'exécute quotidiennement. Idempotence : un horodatage *_notified_at par palier et par
    clinique — même principe que le verrou day_before_reminder_sent_at de
    appointments/tasks.py::send_day_before_reminders, étendu à 4 paliers indépendants. Une
    réexécution le même jour n'envoie jamais deux fois car la vérification exclut les cliniques dont
    l'horodatage pour ce palier est déjà renseigné."""
    today = timezone.localdate()
    active_clinics = Clinic.objects.filter(
        subscription_status__in=[Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.PAST_DUE],
        current_period_end__isnull=False,
    )
    for clinic in active_clinics:
        days_left = (clinic.current_period_end.date() - today).days
        for threshold_days, field_name in _THRESHOLDS:
            if days_left == threshold_days and getattr(clinic, field_name) is None:
                _notify_expiring(clinic=clinic, days_left=threshold_days)
                setattr(clinic, field_name, timezone.now())
                clinic.save(update_fields=[field_name])


def _notify_expiring(*, clinic, days_left):
    # Résolution rôle->utilisateurs façon "Receptionist" — même schéma déjà documenté dans
    # docs/known-issues.md pour appointments/notifications.py : nom de rôle -> tous les
    # utilisateurs correspondants dans la clinique, ce qui peut donner zéro ou plusieurs résultats.
    admins = clinic.users.filter(groups__name="clinic_admin")
    subject = f"Échéance de votre abonnement dans {days_left} jour{'s' if days_left != 1 else ''} — {clinic.name}"
    body = compose_email(
        paragraphs=[
            f"L'abonnement de {clinic.name} arrive à échéance le {clinic.current_period_end.date():%d/%m/%Y} "
            "et sera renouvelé automatiquement.",
            "Afin d'éviter toute interruption de service, nous vous invitons à vérifier que votre moyen "
            "de paiement est à jour depuis l'écran « Abonnement ».",
        ],
        signature=PLATFORM_SIGNATURE,
    )
    for admin_user in admins:
        if admin_user.email:
            send_notification(
                clinic=clinic, recipient_user=admin_user, channel=NotificationLog.Channel.EMAIL,
                notification_type=NotificationLog.NotificationType.SUBSCRIPTION_EXPIRING,
                recipient_address=admin_user.email, subject=subject, body=body,
            )
        # docs/known-issues.md : User n'a pas de champ phone — le canal SMS est aujourd'hui
        # inaccessible pour les destinataires clinic_admin. Non corrigé ici, non contourné
        # silencieusement.


@shared_task
def expire_ended_trials():
    """S'exécute quotidiennement : suspend les cliniques dont le mois d'essai gratuit est terminé
    sans souscription (logique dans services.expire_trials)."""
    from .services import expire_trials

    return expire_trials()

