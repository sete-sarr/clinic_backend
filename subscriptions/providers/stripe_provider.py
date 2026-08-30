import logging

import stripe
from django.conf import settings

from subscriptions.catalog import get_stripe_price_id

from .base import BillingPortalResult, CheckoutSessionResult, PaymentProvider, WebhookEventResult

logger = logging.getLogger(__name__)


class StripePaymentProvider(PaymentProvider):
    """Real Stripe integration — code-complete now, real STRIPE_API_KEY/STRIPE_WEBHOOK_SECRET
    added later (same posture as communication's StubSmsProvider -> real-provider swap per
    docs/roadmap.md). All Stripe SDK usage is confined to this file — never imported elsewhere."""

    name = "stripe"

    def __init__(self):
        stripe.api_key = settings.STRIPE_API_KEY

    def create_checkout_session(self, *, clinic, plan_tier, billing_cycle, success_url, cancel_url):
        try:
            price_id = get_stripe_price_id(plan_tier=plan_tier, billing_cycle=billing_cycle)
            kwargs = {}
            if clinic.stripe_customer_id:
                kwargs["customer"] = clinic.stripe_customer_id
            elif clinic.email:
                kwargs["customer_email"] = clinic.email
            session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=str(clinic.pk),
                metadata={"clinic_id": str(clinic.pk)},
                **kwargs,
            )
            return CheckoutSessionResult(success=True, provider_name=self.name, checkout_url=session.url)
        except Exception as exc:  # noqa: BLE001 — provider boundary, must not raise past here
            logger.exception("Stripe checkout session creation failed for clinic %s", clinic.pk)
            return CheckoutSessionResult(success=False, provider_name=self.name, error_message=str(exc))

    def create_billing_portal_session(self, *, clinic, return_url):
        try:
            if not clinic.stripe_customer_id:
                return BillingPortalResult(
                    success=False,
                    provider_name=self.name,
                    error_message="Clinic has no Stripe customer yet — complete checkout first.",
                )
            session = stripe.billing_portal.Session.create(customer=clinic.stripe_customer_id, return_url=return_url)
            return BillingPortalResult(success=True, provider_name=self.name, portal_url=session.url)
        except Exception as exc:  # noqa: BLE001 — provider boundary
            logger.exception("Stripe billing portal session creation failed for clinic %s", clinic.pk)
            return BillingPortalResult(success=False, provider_name=self.name, error_message=str(exc))

    def construct_webhook_event(self, *, payload, signature):
        try:
            event = stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
            return WebhookEventResult(
                success=True,
                provider_name=self.name,
                event_type=event["type"],
                event_id=event["id"],
                payload=event["data"]["object"],
            )
        except (ValueError, stripe.SignatureVerificationError) as exc:
            return WebhookEventResult(success=False, provider_name=self.name, error_message=str(exc))
        except Exception as exc:  # noqa: BLE001 — provider boundary
            logger.exception("Stripe webhook event construction failed")
            return WebhookEventResult(success=False, provider_name=self.name, error_message=str(exc))
