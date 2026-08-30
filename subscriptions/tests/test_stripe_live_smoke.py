"""
Live-network smoke test against the real Stripe test-mode API (no mocking of stripe.* calls).
Skipped automatically when STRIPE_API_KEY isn't configured, so it never breaks CI/other machines
that don't have a test key in their .env -- this is a deliberate, narrow exception to "always mock
external services" for the purpose of verifying the real integration actually works end-to-end,
not just that our code calls the SDK correctly. Not run as part of the default `manage.py test`
sweep concerns here: it still is (no separate tag), but is self-skipping and network-bound, so keep
it in its own file for easy exclusion (`test --exclude-tag` not used repo-wide, so exclusion today
is by test path: `manage.py test subscriptions --exclude=test_stripe_live_smoke` isn't a thing in
stock Django either -- run explicitly by dotted path when needed).
"""
import unittest

from django.conf import settings
from django.test import TestCase

from clinics.models import Clinic
from common.testing import create_clinic
from subscriptions.providers.stripe_provider import StripePaymentProvider

_HAS_REAL_STRIPE_KEY = bool(settings.STRIPE_API_KEY) and settings.STRIPE_API_KEY.startswith(("sk_test_", "sk_live_"))


@unittest.skipUnless(_HAS_REAL_STRIPE_KEY, "No real STRIPE_API_KEY configured in this environment.")
class StripeLiveSmokeTests(TestCase):
    """Exercises the actual Stripe test-mode API over the network. Creates a throwaway
    Product/Price via the SDK directly (not through our catalog, which reads Price IDs from env
    vars we don't want to require here), then drives it through our real
    StripePaymentProvider.create_checkout_session -- the exact code path CheckoutSessionView calls
    in production, with nothing mocked below the provider boundary."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import stripe

        stripe.api_key = settings.STRIPE_API_KEY
        cls._stripe = stripe
        cls._product = stripe.Product.create(name="[test-suite] Clinic Audit Smoke Test")
        cls._price = stripe.Price.create(
            product=cls._product.id, unit_amount=2900, currency="usd", recurring={"interval": "month"}
        )

    @classmethod
    def tearDownClass(cls):
        try:
            cls._stripe.Product.modify(cls._product.id, active=False)
        except Exception:
            pass
        super().tearDownClass()

    def setUp(self):
        self.clinic = create_clinic()
        self.provider = StripePaymentProvider()

    def test_real_checkout_session_creation(self):
        import subscriptions.providers.stripe_provider as stripe_provider_module

        original_create = self._stripe.checkout.Session.create
        created_sessions = []

        def _capturing_create(*args, **kwargs):
            session = original_create(*args, **kwargs)
            created_sessions.append(session)
            return session

        original_price_fn = stripe_provider_module.get_stripe_price_id
        stripe_provider_module.get_stripe_price_id = lambda *, plan_tier, billing_cycle: self._price.id
        stripe_provider_module.stripe.checkout.Session.create = _capturing_create
        try:
            result = self.provider.create_checkout_session(
                clinic=self.clinic,
                plan_tier=Clinic.PlanTier.STARTER,
                billing_cycle=Clinic.BillingCycle.MONTHLY,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )
        finally:
            stripe_provider_module.get_stripe_price_id = original_price_fn
            stripe_provider_module.stripe.checkout.Session.create = original_create

        self.assertTrue(result.success, f"Real Stripe checkout session creation failed: {result.error_message}")
        self.assertTrue(result.checkout_url.startswith("https://checkout.stripe.com/"))

        # Verify the session Stripe actually created carries the right identifiers for our webhook
        # handler to later resolve the clinic (subscriptions/services.py::_resolve_clinic).
        self.assertEqual(len(created_sessions), 1)
        session = created_sessions[0]
        self.assertEqual(session.client_reference_id, str(self.clinic.pk))
        self.assertEqual(session.metadata["clinic_id"], str(self.clinic.pk))
        self.assertEqual(session.mode, "subscription")

    def test_real_billing_portal_requires_customer_first(self):
        # No stripe_customer_id yet -> provider must fail cleanly without ever calling Stripe.
        result = self.provider.create_billing_portal_session(clinic=self.clinic, return_url="https://example.com")
        self.assertFalse(result.success)

    def test_real_billing_portal_session_with_real_customer(self):
        customer = self._stripe.Customer.create(email=self.clinic.email or "audit@example.com")
        self.clinic.stripe_customer_id = customer.id
        self.clinic.save(update_fields=["stripe_customer_id"])
        try:
            result = self.provider.create_billing_portal_session(
                clinic=self.clinic, return_url="https://example.com"
            )
            # A brand-new Stripe test account usually has no default Billing Portal configuration,
            # so this legitimately fails with a clean provider-level error message in that case --
            # both outcomes are valid; what matters is it never raises past the provider boundary.
            if not result.success:
                self.assertTrue(result.error_message)
            else:
                self.assertTrue(result.portal_url.startswith("https://billing.stripe.com/"))
        finally:
            self._stripe.Customer.delete(customer.id)
