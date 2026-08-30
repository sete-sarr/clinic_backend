import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

from .base import EmailProvider, ProviderResult

logger = logging.getLogger(__name__)


class DjangoEmailProvider(EmailProvider):
    """Wraps Django's own send_mail — honors EMAIL_BACKEND (console backend by default, real
    SMTP is a settings change only, no code change needed here)."""

    name = "django_email"

    def send(self, *, recipient: str, subject: str, body: str) -> ProviderResult:
        try:
            send_mail(subject=subject, message=body, from_email=None, recipient_list=[recipient])
            return ProviderResult(success=True, provider_name=self.name)
        except Exception as exc:  # noqa: BLE001 — provider boundary, must not raise past here
            return ProviderResult(success=False, provider_name=self.name, error_message=str(exc))


class ResendEmailProvider(EmailProvider):
    """Sends via resend.com's HTTP API. See https://resend.com/docs/api-reference/emails/send-email."""

    name = "resend"
    API_URL = "https://api.resend.com/emails"
    TIMEOUT_SECONDS = 10

    def send(self, *, recipient: str, subject: str, body: str) -> ProviderResult:
        try:
            response = requests.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.RESEND_FROM_EMAIL,
                    "to": [recipient],
                    "subject": subject,
                    "text": body,
                },
                timeout=self.TIMEOUT_SECONDS,
            )
            if response.ok:
                return ProviderResult(success=True, provider_name=self.name)
            return ProviderResult(
                success=False,
                provider_name=self.name,
                error_message=f"resend.com returned {response.status_code}: {response.text[:500]}",
            )
        except requests.RequestException as exc:  # noqa: BLE001 — provider boundary, must not raise past here
            return ProviderResult(success=False, provider_name=self.name, error_message=str(exc))


def get_email_provider() -> EmailProvider:
    """communication/tasks.py resolves the email provider through here rather than importing a
    concrete class directly, so a missing Resend key degrades to DjangoEmailProvider (console
    backend by default) instead of crashing delivery — same posture as get_sms_provider()."""
    if settings.RESEND_API_KEY and settings.RESEND_FROM_EMAIL:
        return ResendEmailProvider()
    logger.warning("RESEND_API_KEY/RESEND_FROM_EMAIL not configured — falling back to DjangoEmailProvider.")
    return DjangoEmailProvider()
