from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from clinics.models import Clinic
from common.testing import create_clinic, create_user
from subscriptions.models import SubscriptionEvent
from subscriptions.providers.base import BillingPortalResult, CheckoutSessionResult, WebhookEventResult


class StripeWebhookViewTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()

    @patch("subscriptions.api.views.get_payment_provider")
    def test_valid_signature_applies_transition(self, mock_get_provider):
        mock_get_provider.return_value.construct_webhook_event.return_value = WebhookEventResult(
            success=True, provider_name="stripe", event_type="checkout.session.completed", event_id="evt_ok",
            payload={"metadata": {"clinic_id": str(self.clinic.pk)}, "customer": "cus_1"},
        )
        response = self.client.post(reverse("subscriptions-webhook"), data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.ACTIVE)

    @patch("subscriptions.api.views.get_payment_provider")
    def test_invalid_signature_returns_400_and_does_not_change_clinic(self, mock_get_provider):
        mock_get_provider.return_value.construct_webhook_event.return_value = WebhookEventResult(
            success=False, provider_name="stripe", error_message="bad signature",
        )
        response = self.client.post(reverse("subscriptions-webhook"), data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.TRIAL)

    @patch("subscriptions.api.views.get_payment_provider")
    def test_no_auth_header_still_reaches_view_logic(self, mock_get_provider):
        mock_get_provider.return_value.construct_webhook_event.return_value = WebhookEventResult(
            success=False, provider_name="stripe", error_message="missing signature",
        )
        response = self.client.post(reverse("subscriptions-webhook"), data={}, format="json")
        self.assertNotEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("subscriptions.api.views.get_payment_provider")
    def test_duplicate_delivery_is_idempotent(self, mock_get_provider):
        mock_get_provider.return_value.construct_webhook_event.return_value = WebhookEventResult(
            success=True, provider_name="stripe", event_type="checkout.session.completed", event_id="evt_dup2",
            payload={"metadata": {"clinic_id": str(self.clinic.pk)}, "customer": "cus_2"},
        )
        self.client.post(reverse("subscriptions-webhook"), data={}, format="json")
        self.client.post(reverse("subscriptions-webhook"), data={}, format="json")
        self.assertEqual(SubscriptionEvent.objects.filter(clinic=self.clinic).count(), 1)


class CheckoutSessionViewTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.clinic_admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.secretary = create_user(clinic=self.clinic, role="secretary")

    def test_requires_clinic_admin(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.post(reverse("subscriptions-checkout-session"), {"plan_tier": "starter", "billing_cycle": "monthly"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("subscriptions.providers.get_payment_provider")
    def test_returns_checkout_url(self, mock_get_provider):
        mock_get_provider.return_value.create_checkout_session.return_value = CheckoutSessionResult(
            success=True, provider_name="stripe", checkout_url="https://checkout.stripe.com/xyz",
        )
        self.client.force_authenticate(self.clinic_admin)
        response = self.client.post(
            reverse("subscriptions-checkout-session"), {"plan_tier": "starter", "billing_cycle": "monthly"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["checkout_url"], "https://checkout.stripe.com/xyz")


class BillingPortalViewTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.clinic_admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.secretary = create_user(clinic=self.clinic, role="secretary")

    def test_requires_clinic_admin(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.post(reverse("subscriptions-billing-portal"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("subscriptions.providers.get_payment_provider")
    def test_returns_portal_url(self, mock_get_provider):
        mock_get_provider.return_value.create_billing_portal_session.return_value = BillingPortalResult(
            success=True, provider_name="stripe", portal_url="https://billing.stripe.com/xyz"
        )
        self.client.force_authenticate(self.clinic_admin)
        response = self.client.post(reverse("subscriptions-billing-portal"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["portal_url"], "https://billing.stripe.com/xyz")
