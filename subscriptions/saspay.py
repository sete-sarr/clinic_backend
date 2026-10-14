"""Client de l'API SasPay (https://docs.saspay.me) — paiement Mobile Money / carte de l'abonnement
plateforme. Aucun appel SasPay hors de ce fichier ; aucune règle métier ici (subscriptions/prepaid.py).

- POST /checkout-sessions/ : page de paiement hébergée (le client choisit Wave, Orange Money…).
- GET /checkout-sessions/{id}/status/ : statut revérifié auprès de l'opérateur.
- GET /payments/{id}/verify/ : détail d'une transaction, revérifié auprès de l'opérateur.
- Webhooks : signature HMAC-SHA256 hexadécimale de « <timestamp>.<corps brut> », en-têtes
  X-Webhook-Signature et X-Webhook-Timestamp, refus au-delà de 5 minutes d'écart.
"""

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
WEBHOOK_TOLERANCE_SECONDS = 5 * 60


class SasPayError(Exception):
    """Appel SasPay impossible ou refusé ; le message technique reste dans les journaux."""


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    checkout_url: str


@dataclass(frozen=True)
class SessionStatus:
    status: str  # PENDING, PAID…
    transaction_id: str


@dataclass(frozen=True)
class Transaction:
    id: str
    status: str  # PENDING, SUCCESS, FAILED, CANCELLED
    requested_amount: str
    debited_amount: str
    currency: str
    network: str  # code du réseau (ex. wave_sn), confirmé par un vrai paiement du 2026-10-14
    reference: str
    checkout_session: str = ""  # session de paiement d'origine : rattachement direct au paiement proCli


# Codes réseau SasPay -> libellé affiché (préfixe du code, ex. wave_sn, orange_money_sn).
_NETWORK_LABELS = (("wave", "Wave"), ("orange", "Orange Money"), ("om_", "Orange Money"), ("free", "YAS"),
                   ("yas", "YAS"), ("wizall", "Wizall"), ("card", "Carte"), ("visa", "Carte"), ("master", "Carte"))


def network_label(code):
    code = (code or "").lower()
    return next((label for prefix, label in _NETWORK_LABELS if code.startswith(prefix)), code)


def _request(method, path, **kwargs):
    if not settings.SASPAY_API_KEY:
        raise SasPayError("SASPAY_API_KEY n'est pas configurée.")
    try:
        response = requests.request(
            method, f"{settings.SASPAY_API_URL}{path}", timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {settings.SASPAY_API_KEY}", "Accept": "application/json"},
            **kwargs,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.exception("Appel SasPay %s %s impossible", method, path)
        raise SasPayError(str(exc)) from exc
    if response.status_code >= 400 or not body.get("success", False):
        logger.error("SasPay %s %s a répondu %s : %s", method, path, response.status_code, body.get("error"))
        raise SasPayError(f"HTTP {response.status_code}")
    return body.get("data") or {}


def create_checkout_session(*, amount, customer_email, customer_name, description, return_url, metadata):
    """`amount` en XOF (entier). Frais à la charge du client (mode ADD_ON, décision du 2026-10-14)."""
    data = _request("POST", "/checkout-sessions/", json={
        "amount": f"{int(amount)}.00",
        "currency": "XOF",
        "country": "SN",
        "customer_email": customer_email,
        "customer_name": customer_name,
        "description": description,
        "return_url": return_url,
        "metadata": metadata,
        "fee_charge_mode": "ADD_ON",
    })
    return CheckoutSession(id=str(data["id"]), checkout_url=data["checkout_url"])


def get_session_status(session_id):
    data = _request("GET", f"/checkout-sessions/{session_id}/status/")
    return SessionStatus(status=str(data.get("status") or ""), transaction_id=str(data.get("transaction_id") or ""))


def verify_transaction(transaction_id):
    data = _request("GET", f"/payments/{transaction_id}/verify/")
    return Transaction(
        id=str(data.get("id") or transaction_id),
        status=str(data.get("status") or ""),
        requested_amount=str(data.get("requested_amount") or "0"),
        debited_amount=str(data.get("debited_amount") or "0"),
        currency=str(data.get("currency") or ""),
        network=str(data.get("network_code") or ""),
        reference=str(data.get("reference") or ""),
        checkout_session=str(data.get("checkout_session") or ""),
    )


def webhook_signature_is_valid(*, body: bytes, signature: str, timestamp: str, now=None) -> bool:
    secret = settings.SASPAY_WEBHOOK_SECRET
    if not secret or not signature or not timestamp:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs((now or time.time()) - sent_at) > WEBHOOK_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())
