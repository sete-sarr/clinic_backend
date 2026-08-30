from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class CheckoutSessionResult:
    success: bool
    provider_name: str
    checkout_url: str = ""
    error_message: str = ""


@dataclass
class BillingPortalResult:
    success: bool
    provider_name: str
    portal_url: str = ""
    error_message: str = ""


@dataclass
class WebhookEventResult:
    success: bool
    provider_name: str
    event_type: str = ""
    event_id: str = ""
    payload: dict | None = None
    error_message: str = ""


class PaymentProvider(ABC):
    """Mirrors communication/providers/base.py's shape: one ABC per capability set, keyword-only
    args, small result dataclasses (never a raw dict), broad exception catch only at the provider
    boundary. docs/subscription-billing.md §5: no direct SDK calls outside a provider class."""

    @abstractmethod
    def create_checkout_session(
        self, *, clinic, plan_tier: str, billing_cycle: str, success_url: str, cancel_url: str
    ) -> CheckoutSessionResult: ...

    @abstractmethod
    def create_billing_portal_session(self, *, clinic, return_url: str) -> BillingPortalResult: ...

    @abstractmethod
    def construct_webhook_event(self, *, payload: bytes, signature: str) -> WebhookEventResult: ...
