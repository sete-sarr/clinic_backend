import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import InAppNotification, NotificationLog
from .providers.email_provider import get_email_provider
from .providers.sms_provider import get_sms_provider


def _resolve_provider(channel):
    # Résolu à chaque envoi, pas à l'import, afin que les changements de *_API_KEY prennent effet
    # sans redémarrage du worker et que les tests puissent surcharger les settings librement.
    if channel == NotificationLog.Channel.EMAIL:
        return get_email_provider()
    if channel == NotificationLog.Channel.SMS:
        return get_sms_provider()
    raise ValueError(f"No provider configured for channel {channel!r}")


logger = logging.getLogger(__name__)

# Nombre maximal d'envois d'un même message (premier envoi + nouvelles tentatives).
MAX_DELIVERY_ATTEMPTS = 3
# Les messages en échec sont retentés par la tâche quotidienne s'ils ont moins de 24 h : au-delà,
# un rappel ou un code de vérification n'a plus de sens.
RETRY_WINDOW = timedelta(hours=24)


def _deliver(log):
    """Envoie le message via son fournisseur et met à jour le NotificationLog. Renvoie True si
    l'envoi a réussi."""
    provider = _resolve_provider(log.channel)
    # Un SMS n'a pas d'objet (SmsProvider.send, providers/base.py) : ne le transmettre qu'à l'e-mail.
    if log.channel == NotificationLog.Channel.EMAIL:
        result = provider.send(recipient=log.recipient_address, subject=log.subject, body=log.body)
    else:
        result = provider.send(recipient=log.recipient_address, body=log.body)

    if result.success:
        log.status = NotificationLog.Status.SENT
        log.provider_name = result.provider_name
        log.sent_at = timezone.now()
        log.error_message = ""
        log.save(update_fields=["status", "provider_name", "sent_at", "error_message"])
        return True

    log.status = NotificationLog.Status.FAILED
    log.provider_name = result.provider_name
    log.error_message = result.error_message
    log.retry_count += 1
    log.save(update_fields=["status", "provider_name", "error_message", "retry_count"])
    return False


def deliver_now(notification_log_id):
    """Envoi direct, sans worker (NOTIFICATIONS_VIA_CELERY faux) : appelé après la validation de la
    transaction ; une erreur du fournisseur ne fait jamais échouer la requête de l'utilisateur. Un
    échec est retenté par la tâche quotidienne (retry_failed_notifications)."""
    log = NotificationLog.objects.filter(pk=notification_log_id).first()
    if log is None:
        return
    try:
        _deliver(log)
    except Exception:  # fournisseur injoignable, réponse inattendue…
        logger.exception("Envoi de la notification %s impossible", notification_log_id)
        NotificationLog.objects.filter(pk=log.pk, status=NotificationLog.Status.PENDING).update(
            status=NotificationLog.Status.FAILED, error_message="Exception lors de l'envoi", retry_count=1,
        )


def retry_failed_notifications(now=None):
    """Tâche quotidienne : nouvelle tentative pour les messages récents en échec ou restés en attente."""
    now = now or timezone.now()
    retried = 0
    for log in NotificationLog.objects.filter(
        status__in=[NotificationLog.Status.FAILED, NotificationLog.Status.PENDING],
        created_at__gte=now - RETRY_WINDOW, created_at__lte=now - timedelta(minutes=5),
        retry_count__lt=MAX_DELIVERY_ATTEMPTS,
    ):
        deliver_now(log.pk)
        retried += 1
    return retried


@shared_task(bind=True, max_retries=MAX_DELIVERY_ATTEMPTS - 1)
def deliver_notification(self, notification_log_id):
    """Envoi par un worker Celery (NOTIFICATIONS_VIA_CELERY), avec backoff exponentiel
    (docs/communication-architecture.md)."""
    try:
        log = NotificationLog.objects.get(pk=notification_log_id)
    except NotificationLog.DoesNotExist:
        return
    if not _deliver(log):
        raise self.retry(countdown=2**self.request.retries * 30)


# Notifications archivées conservées 90 jours, puis supprimées (business/notification-rules.md
# § CANAL IN-APP).
ARCHIVED_IN_APP_RETENTION_DAYS = 90


@shared_task
def purge_archived_in_app_notifications():
    limit = timezone.now() - timedelta(days=ARCHIVED_IN_APP_RETENTION_DAYS)
    deleted, _ = InAppNotification.objects.filter(archived_at__lt=limit).delete()
    return deleted

