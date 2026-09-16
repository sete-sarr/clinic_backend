from celery import shared_task
from django.utils import timezone

from .models import NotificationLog
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


@shared_task(bind=True, max_retries=3)
def deliver_notification(self, notification_log_id):
    try:
        log = NotificationLog.objects.get(pk=notification_log_id)
    except NotificationLog.DoesNotExist:
        return

    provider = _resolve_provider(log.channel)
    result = provider.send(recipient=log.recipient_address, subject=log.subject, body=log.body)

    if result.success:
        log.status = NotificationLog.Status.SENT
        log.provider_name = result.provider_name
        log.sent_at = timezone.now()
        log.error_message = ""
        log.save(update_fields=["status", "provider_name", "sent_at", "error_message"])
        return

    log.status = NotificationLog.Status.FAILED
    log.provider_name = result.provider_name
    log.error_message = result.error_message
    log.retry_count += 1
    log.save(update_fields=["status", "provider_name", "error_message", "retry_count"])
    # Backoff exponentiel (docs/communication-architecture.md : nouvelle tentative avec backoff exponentiel).
    raise self.retry(countdown=2**self.request.retries * 30)
