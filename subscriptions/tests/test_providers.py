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
        self.assertIn("stripe down", result.error_message)

    def test_create_checkout_session_missing_price_id_returns_failure(self):
        result = self.provider.create_checkout_session(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.ENTERPRISE, billing_cycle=Clinic.BillingCycle.ANNUAL,
            success_url="https://example.com/success", cancel_url="https://example.com/cancel",
        )
        self.assertFalse(result.success)

    def test_create_billing_portal_session_no_customer_id_returns_failure(self):
        result = self.provider.create_billing_portal_session(clinic=self.clinic, return_url="https://example.com")
        self.assertFalse(result.success)
        self.assertIn("Stripe customer", result.error_message)

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
