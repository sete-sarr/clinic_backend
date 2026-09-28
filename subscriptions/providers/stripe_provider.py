import logging
from datetime import timedelta

import stripe
from django.conf import settings
from django.utils import timezone

from clinics.models import Clinic
from subscriptions.catalog import get_stripe_price_id

from .base import BillingPortalResult, CheckoutSessionResult, PaymentProvider, PlanChangeResult, WebhookEventResult

logger = logging.getLogger(__name__)

# Stripe Checkout refuse un subscription_data.trial_end à moins de 48 h dans le futur.
_STRIPE_MIN_TRIAL_END = timedelta(hours=48)

# Message affiché à l'administrateur de clinique ; le détail technique (exception Stripe, Price ID
# manquant...) reste uniquement dans les logs (logger.exception ci-dessous).
_UNAVAILABLE_MESSAGE = "Le paiement en ligne est momentanément indisponible. Réessayez plus tard."


def _remaining_trial_end(clinic):
    """Fin d'essai à reporter sur l'abonnement Stripe, pour qu'une clinique qui souscrit pendant
    son mois gratuit ne soit prélevée qu'à la fin de celui-ci. None si l'essai est terminé ou s'il
    en reste moins de 48 h (prélèvement immédiat dans ce cas)."""
    trial_ends_at = getattr(clinic, "trial_ends_at", None)
    if clinic.subscription_status != Clinic.SubscriptionStatus.TRIAL or not trial_ends_at:
        return None
    if trial_ends_at - timezone.now() < _STRIPE_MIN_TRIAL_END:
        return None
    return int(trial_ends_at.timestamp())


class StripePaymentProvider(PaymentProvider):
    """Véritable intégration Stripe — code complet dès maintenant, STRIPE_API_KEY/STRIPE_WEBHOOK_SECRET
    réels ajoutés plus tard (même logique que le remplacement StubSmsProvider -> fournisseur réel de
    communication, selon docs/roadmap.md). Toute utilisation du SDK Stripe est confinée à ce fichier
    — jamais importée ailleurs."""

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
            trial_end = _remaining_trial_end(clinic)
            if trial_end:
                kwargs["subscription_data"] = {"trial_end": trial_end}
            session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=str(clinic.pk),
                # plan_tier/billing_cycle relus par services._on_checkout_completed pour enregistrer la
                # formule effectivement souscrite sur la clinique.
                metadata={"clinic_id": str(clinic.pk), "plan_tier": plan_tier, "billing_cycle": billing_cycle},
                **kwargs,
            )
            return CheckoutSessionResult(success=True, provider_name=self.name, checkout_url=session.url)
        except Exception:  # noqa: BLE001 — provider boundary, must not raise past here
            logger.exception("Stripe checkout session creation failed for clinic %s", clinic.pk)
            return CheckoutSessionResult(success=False, provider_name=self.name, error_message=_UNAVAILABLE_MESSAGE)

    def create_billing_portal_session(self, *, clinic, return_url):
        try:
            if not clinic.stripe_customer_id:
                return BillingPortalResult(
                    success=False,
                    provider_name=self.name,
                    error_message="Aucun abonnement payant n'est encore associé à cette clinique : choisissez d'abord une formule.",
                )
            session = stripe.billing_portal.Session.create(customer=clinic.stripe_customer_id, return_url=return_url)
            return BillingPortalResult(success=True, provider_name=self.name, portal_url=session.url)
        except Exception:  # noqa: BLE001 — provider boundary
            logger.exception("Stripe billing portal session creation failed for clinic %s", clinic.pk)
            return BillingPortalResult(success=False, provider_name=self.name, error_message=_UNAVAILABLE_MESSAGE)

    def change_subscription_plan(self, *, clinic, plan_tier, billing_cycle):
        """Remplace le Price de l'unique ligne de l'abonnement existant. Proratisation Stripe par
        défaut (create_prorations) : l'écart est porté sur la prochaine facture ; un passage
        mensuel <-> annuel change l'intervalle, Stripe facture alors immédiatement la nouvelle
        période en créditant le reste de l'ancienne."""
        try:
            price_id = get_stripe_price_id(plan_tier=plan_tier, billing_cycle=billing_cycle)
            subscription = stripe.Subscription.retrieve(clinic.stripe_subscription_id)
            item_id = subscription["items"]["data"][0]["id"]
            stripe.Subscription.modify(
                clinic.stripe_subscription_id,
                items=[{"id": item_id, "price": price_id}],
                proration_behavior="create_prorations",
                metadata={"clinic_id": str(clinic.pk), "plan_tier": plan_tier, "billing_cycle": billing_cycle},
            )
            return PlanChangeResult(success=True, provider_name=self.name)
        except Exception:  # noqa: BLE001 — provider boundary
            logger.exception("Stripe plan change failed for clinic %s", clinic.pk)
            return PlanChangeResult(success=False, provider_name=self.name, error_message=_UNAVAILABLE_MESSAGE)

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
