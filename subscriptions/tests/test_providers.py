from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from clinics.models import Clinic
from common.testing import create_clinic
from subscriptions.providers import get_payment_provider
from subscriptions.providers.stripe_provider import StripePaymentProvider


class GetPaymentProviderTests(TestCase):
    def test_resolves_stripe_by_default_setting(self):
        provider = get_payment_provider()
        self.assertIsInstance(provider, StripePaymentProvider)

    @override_settings(SUBSCRIPTION_PAYMENT_PROVIDER="stripe")
    def test_respects_settings_override(self):
        provider = get_payment_provider()
        self.assertIsInstance(provider, StripePaymentProvider)


@override_settings(
    STRIPE_PRICE_STARTER_MONTHLY="price_starter_monthly",
    STRIPE_PRICE_STARTER_ANNUAL="price_starter_annual",
    STRIPE_PRICE_ENTERPRISE_ANNUAL="price_enterprise_annual",
    # Volontairement laissé non défini : test_create_checkout_session_missing_price_id_returns_failure
    # a besoin d'une paire tier/cycle réelle sans Price ID configuré pour exercer le chemin d'échec.
    STRIPE_PRICE_ENTERPRISE_MONTHLY="",
)
class StripePaymentProviderTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.provider = StripePaymentProvider()

    @patch("subscriptions.providers.stripe_provider.stripe.checkout.Session.create")
    def test_create_checkout_session_success(self, mock_create):
        mock_create.return_value = MagicMock(url="https://checkout.stripe.com/session/xyz")
        result = self.provider.create_checkout_session(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.STARTER, billing_cycle=Clinic.BillingCycle.MONTHLY,
            success_url="https://example.com/success", cancel_url="https://example.com/cancel",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.checkout_url, "https://checkout.stripe.com/session/xyz")

    @patch("subscriptions.providers.stripe_provider.stripe.checkout.Session.create")
    def test_create_checkout_session_stripe_error_returns_failure_result(self, mock_create):
        mock_create.side_effect = RuntimeError("stripe down")
        result = self.provider.create_checkout_session(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.STARTER, billing_cycle=Clinic.BillingCycle.MONTHLY,
            success_url="https://example.com/success", cancel_url="https://example.com/cancel",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "Le paiement en ligne est momentanément indisponible. Réessayez plus tard.")

    @patch("subscriptions.providers.stripe_provider.stripe.checkout.Session.create")
    def test_create_checkout_session_enterprise_annual_success(self, mock_create):
        mock_create.return_value = MagicMock(url="https://checkout.stripe.com/session/enterprise")
        result = self.provider.create_checkout_session(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.ENTERPRISE, billing_cycle=Clinic.BillingCycle.ANNUAL,
            success_url="https://example.com/success", cancel_url="https://example.com/cancel",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.checkout_url, "https://checkout.stripe.com/session/enterprise")

    def test_create_checkout_session_missing_price_id_returns_failure(self):
        result = self.provider.create_checkout_session(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.ENTERPRISE, billing_cycle=Clinic.BillingCycle.MONTHLY,
            success_url="https://example.com/success", cancel_url="https://example.com/cancel",
        )
        self.assertFalse(result.success)

    def test_create_billing_portal_session_no_customer_id_returns_failure(self):
        result = self.provider.create_billing_portal_session(clinic=self.clinic, return_url="https://example.com")
        self.assertFalse(result.success)
        self.assertIn("choisissez d'abord une formule", result.error_message)

    @patch("subscriptions.providers.stripe_provider.stripe.billing_portal.Session.create")
    def test_create_billing_portal_session_success(self, mock_create):
        self.clinic.stripe_customer_id = "cus_abc"
        self.clinic.save()
        mock_create.return_value = MagicMock(url="https://billing.stripe.com/session/abc")
        result = self.provider.create_billing_portal_session(clinic=self.clinic, return_url="https://example.com")
        self.assertTrue(result.success)
        self.assertEqual(result.portal_url, "https://billing.stripe.com/session/abc")

    @patch("subscriptions.providers.stripe_provider.stripe.Webhook.construct_event")
    def test_construct_webhook_event_valid_signature(self, mock_construct):
        mock_construct.return_value = {
            "id": "evt_1", "type": "checkout.session.completed", "data": {"object": {"customer": "cus_1"}},
        }
        result = self.provider.construct_webhook_event(payload=b"{}", signature="sig")
        self.assertTrue(result.success)
        self.assertEqual(result.event_type, "checkout.session.completed")
        self.assertEqual(result.event_id, "evt_1")

    def test_construct_webhook_event_invalid_signature_returns_failure(self):
        result = self.provider.construct_webhook_event(payload=b"not json", signature="bad-sig")
        self.assertFalse(result.success)


@override_settings(STRIPE_PRICE_STARTER_MONTHLY="price_starter_monthly")
class StripeCheckoutTrialCarryOverTests(TestCase):
    """Une clinique qui souscrit pendant son mois gratuit n'est prélevée qu'à la fin de l'essai."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        self.clinic = create_clinic()
        self.clinic.subscription_status = Clinic.SubscriptionStatus.TRIAL
        self.now = timezone.now()
        self.timedelta = timedelta
        self.provider = StripePaymentProvider()

    def _checkout(self, mock_create):
        mock_create.return_value = MagicMock(url="https://checkout.stripe.com/session/x")
        self.provider.create_checkout_session(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.STARTER, billing_cycle=Clinic.BillingCycle.MONTHLY,
            success_url="https://app/ok", cancel_url="https://app/ko",
        )
        return mock_create.call_args.kwargs

    @patch("subscriptions.providers.stripe_provider.stripe.checkout.Session.create")
    def test_remaining_trial_is_carried_to_stripe(self, mock_create):
        self.clinic.trial_ends_at = self.now + self.timedelta(days=20)
        kwargs = self._checkout(mock_create)
        self.assertEqual(kwargs["subscription_data"], {"trial_end": int(self.clinic.trial_ends_at.timestamp())})

    @patch("subscriptions.providers.stripe_provider.stripe.checkout.Session.create")
    def test_less_than_48h_left_charges_immediately(self, mock_create):
        self.clinic.trial_ends_at = self.now + self.timedelta(hours=20)
        kwargs = self._checkout(mock_create)
        self.assertNotIn("subscription_data", kwargs)

    @patch("subscriptions.providers.stripe_provider.stripe.checkout.Session.create")
    def test_suspended_clinic_is_charged_immediately(self, mock_create):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic.trial_ends_at = self.now - self.timedelta(days=2)
        kwargs = self._checkout(mock_create)
        self.assertNotIn("subscription_data", kwargs)


@override_settings(STRIPE_PRICE_PROFESSIONAL_ANNUAL="price_pro_annual")
class StripeChangeSubscriptionPlanTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.clinic.stripe_subscription_id = "sub_123"
        self.provider = StripePaymentProvider()

    @patch("subscriptions.providers.stripe_provider.stripe.Subscription.modify")
    @patch("subscriptions.providers.stripe_provider.stripe.Subscription.retrieve")
    def test_replaces_the_price_of_the_existing_item(self, mock_retrieve, mock_modify):
        mock_retrieve.return_value = {"items": {"data": [{"id": "si_1"}]}}
        result = self.provider.change_subscription_plan(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.PROFESSIONAL, billing_cycle=Clinic.BillingCycle.ANNUAL
        )
        self.assertTrue(result.success)
        args, kwargs = mock_modify.call_args
        self.assertEqual(args, ("sub_123",))
        self.assertEqual(kwargs["items"], [{"id": "si_1", "price": "price_pro_annual"}])
        self.assertEqual(kwargs["proration_behavior"], "create_prorations")

    @patch("subscriptions.providers.stripe_provider.stripe.Subscription.retrieve", side_effect=RuntimeError("down"))
    def test_stripe_error_returns_french_failure(self, _mock_retrieve):
        result = self.provider.change_subscription_plan(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.PROFESSIONAL, billing_cycle=Clinic.BillingCycle.ANNUAL
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "Le paiement en ligne est momentanément indisponible. Réessayez plus tard.")
