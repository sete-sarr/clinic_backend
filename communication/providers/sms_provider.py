import logging

import requests
from django.conf import settings

from .base import ProviderResult, SmsProvider

logger = logging.getLogger(__name__)


class StubSmsProvider(SmsProvider):
    """
    Stand-in for a real SMS provider — used wherever no provider is configured (docs/known-issues.md).
    Logs instead of sending and always "succeeds", so the rest of the communication layer (retry,
    audit, OTP flow) can be built and tested against a real interface without live credentials.
    """

    name = "stub_sms"

    def send(self, *, recipient: str, body: str) -> ProviderResult:
        logger.info("STUB SMS to %s: %s", recipient, body)
        return ProviderResult(success=True, provider_name=self.name)


class HttpSmsProvider(SmsProvider):
    """Sends via httpsms.com — relays through the Android device registered as HTTPSMS_FROM_NUMBER
    on the httpsms.com account. See https://httpsms.com/docs/api for the API contract."""

    name = "httpsms"
    API_URL = "https://api.httpsms.com/v1/messages/send"
    TIMEOUT_SECONDS = 10

    def send(self, *, recipient: str, body: str) -> ProviderResult:
        try:
            response = requests.post(
                self.API_URL,
                headers={"x-api-key": settings.HTTPSMS_API_KEY, "Content-Type": "application/json"},
                json={"from": settings.HTTPSMS_FROM_NUMBER, "to": recipient, "content": body},
                timeout=self.TIMEOUT_SECONDS,
            )
            if response.ok:
                return ProviderResult(success=True, provider_name=self.name)
            return ProviderResult(
                success=False,
                provider_name=self.name,
                error_message=f"httpsms.com returned {response.status_code}: {response.text[:500]}",
            )
        except requests.RequestException as exc:  # noqa: BLE001 — provider boundary, must not raise past here
            return ProviderResult(success=False, provider_name=self.name, error_message=str(exc))


def get_sms_provider() -> SmsProvider:
    """communication/tasks.py resolves the SMS provider through here rather than importing a
    concrete class directly, so missing credentials degrade to the stub instead of crashing
    delivery (same "architecture ready, credentials not" posture as Stripe in settings.py)."""
    if settings.HTTPSMS_API_KEY and settings.HTTPSMS_FROM_NUMBER:
        return HttpSmsProvider()
    logger.warning("HTTPSMS_API_KEY/HTTPSMS_FROM_NUMBER not configured — falling back to StubSmsProvider.")
    return StubSmsProvider()
