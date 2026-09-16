import logging

import requests
from django.conf import settings

from .base import ProviderResult, SmsProvider

logger = logging.getLogger(__name__)


class StubSmsProvider(SmsProvider):
    """
    Substitut à un véritable fournisseur SMS — utilisé partout où aucun fournisseur n'est configuré
    (docs/known-issues.md). Journalise au lieu d'envoyer et "réussit" toujours, afin que le reste de
    la couche de communication (nouvelle tentative, audit, flux OTP) puisse être construit et testé
    contre une interface réelle sans identifiants réels.
    """

    name = "stub_sms"

    def send(self, *, recipient: str, body: str) -> ProviderResult:
        logger.info("STUB SMS to %s: %s", recipient, body)
        return ProviderResult(success=True, provider_name=self.name)


class HttpSmsProvider(SmsProvider):
    """Envoie via httpsms.com — relaie via l'appareil Android enregistré comme HTTPSMS_FROM_NUMBER
    sur le compte httpsms.com. Voir https://httpsms.com/docs/api pour le contrat d'API."""

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
    """communication/tasks.py résout le fournisseur SMS en passant par ici plutôt qu'en important
    directement une classe concrète, de sorte que des identifiants manquants dégradent vers le
    stub au lieu de faire planter l'envoi (même posture "architecture prête, identifiants non"
    que Stripe dans settings.py)."""
    if settings.HTTPSMS_API_KEY and settings.HTTPSMS_FROM_NUMBER:
        return HttpSmsProvider()
    logger.warning("HTTPSMS_API_KEY/HTTPSMS_FROM_NUMBER not configured — falling back to StubSmsProvider.")
    return StubSmsProvider()
