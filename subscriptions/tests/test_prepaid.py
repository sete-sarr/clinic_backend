"""Abonnement prépayé par SasPay (subscriptions/prepaid.py) : montant fixé côté serveur, activation
seulement après revérification, règles de période, webhook signé, échéance et délai de grâce. Les
appels SasPay sont simulés."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from clinics.models import Clinic
from common.testing import create_clinic, create_user
from communication.models import NotificationLog
from subscriptions import prepaid, saspay
from subscriptions.models import SubscriptionPayment

SESSION = saspay.CheckoutSession(id="sess-1", checkout_url="https://pay.saspay.me/c/abc")


def _transaction(amount="10000.00", status_="SUCCESS", txn_id="txn-1"):
    return saspay.Transaction(id=txn_id, status=status_, requested_amount=amount, debited_amount="10225.00",
                              currency="XOF", network="wave_sn", reference="TXN-1")


@override_settings(SUBSCRIPTION_PAYMENT_PROVIDER="saspay")
class PrepaidCheckoutTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Prépayée")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin", email="admin@clinique.example")
        self.client.force_authenticate(self.admin)

    @mock.patch("subscriptions.prepaid.saspay.create_checkout_session", return_value=SESSION)
    def test_checkout_amount_is_set_by_the_server(self, create):
        response = self.client.post(reverse("subscriptions-checkout-session"),
                                    {"plan_tier": "professional", "billing_cycle": "annual", "amount": 1}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["checkout_url"], SESSION.checkout_url)
        payment = SubscriptionPayment.objects.get(pk=response.data["payment_id"])
        self.assertEqual(payment.amount, Decimal("135000"))  # 15 000 x 9 mois (3 offerts)
        self.assertEqual(create.call_args.kwargs["amount"], Decimal("135000"))
        self.assertIn(f"payment={payment.pk}", create.call_args.kwargs["return_url"])

    @mock.patch("subscriptions.prepaid.saspay.create_checkout_session", side_effect=saspay.SasPayError("down"))
    def test_unavailable_gateway_gives_a_clear_message(self, _create):
        response = self.client.post(reverse("subscriptions-checkout-session"),
                                    {"plan_tier": "starter", "billing_cycle": "monthly"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SubscriptionPayment.objects.get().status, SubscriptionPayment.Status.FAILED)

    def test_other_clinics_cannot_refresh_my_payment(self):
        payment = SubscriptionPayment.objects.create(clinic=self.clinic, plan_tier="starter", billing_cycle="monthly",
                                                     amount=Decimal("10000"), provider_session_id="sess-1")
        self.client.force_authenticate(create_user(clinic=create_clinic("Autre"), role="clinic_admin"))
        response = self.client.post(reverse("subscriptions-payment-refresh", args=[payment.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class PrepaidConfirmationTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Paiement")
        self.clinic.trial_ends_at = timezone.now() + timedelta(days=6)
        self.clinic.save()
        self.admin = create_user(clinic=self.clinic, role="clinic_admin", email="admin@paiement.example")

    def _payment(self, plan="starter", cycle="monthly", amount="10000"):
        return SubscriptionPayment.objects.create(clinic=self.clinic, plan_tier=plan, billing_cycle=cycle,
                                                  amount=Decimal(amount), provider_session_id="sess-1")

    def _pay(self, payment, txn=None):
        with mock.patch("subscriptions.prepaid.saspay.get_session_status",
                        return_value=saspay.SessionStatus(status="PAID", transaction_id="txn-1")), \
             mock.patch("subscriptions.prepaid.saspay.verify_transaction", return_value=txn or _transaction()), \
             self.captureOnCommitCallbacks(execute=True):
            return prepaid.refresh_payment(payment)

    def test_payment_during_trial_activates_and_keeps_the_free_days(self):
        payment = self._pay(self._payment())
        self.clinic.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionPayment.Status.PAID)
        self.assertEqual(payment.charged_amount, Decimal("10225.00"))
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.ACTIVE)
        self.assertEqual(payment.period_start, self.clinic.trial_ends_at)
        self.assertEqual(self.clinic.current_period_end, prepaid.add_months(self.clinic.trial_ends_at, 1))
        email = NotificationLog.objects.get(notification_type=NotificationLog.NotificationType.SUBSCRIPTION_PAYMENT)
        self.assertEqual(email.recipient_address, "admin@paiement.example")
        # Une seconde vérification n'ajoute rien.
        end = self.clinic.current_period_end
        self._pay(payment)
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.current_period_end, end)

    def test_renewal_of_the_same_plan_extends_from_the_current_end(self):
        end = timezone.now() + timedelta(days=5)
        Clinic.objects.filter(pk=self.clinic.pk).update(subscription_status="active", current_period_end=end)
        self._pay(self._payment())
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.current_period_end, prepaid.add_months(end, 1))

    def test_plan_change_starts_a_new_period_today(self):
        Clinic.objects.filter(pk=self.clinic.pk).update(subscription_status="active",
                                                        current_period_end=timezone.now() + timedelta(days=20))
        before = timezone.now()
        payment = self._pay(self._payment(plan="professional", amount="15000"), _transaction(amount="15000.00"))
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.plan_tier, Clinic.PlanTier.PROFESSIONAL)
        self.assertGreaterEqual(payment.period_start, before)
        self.assertLess(self.clinic.current_period_end, timezone.now() + timedelta(days=32))

    def test_unexpected_amount_or_failed_payment_never_activates(self):
        payment = self._pay(self._payment(), _transaction(amount="100.00"))
        self.assertEqual(payment.status, SubscriptionPayment.Status.PENDING)
        payment = self._pay(payment, _transaction(status_="FAILED"))
        self.assertEqual(payment.status, SubscriptionPayment.Status.FAILED)
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.TRIAL)

    def test_add_months_keeps_the_end_of_month(self):
        moment = datetime(2027, 1, 31, 9, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(prepaid.add_months(moment, 1).day, 28)
        self.assertEqual(prepaid.add_months(moment, 12), datetime(2028, 1, 31, 9, 0, tzinfo=dt_timezone.utc))


@override_settings(SASPAY_WEBHOOK_SECRET="whsec-test")
class SasPayWebhookTests(APITestCase):
    url = "/api/v1/subscriptions/saspay/webhook/"

    def setUp(self):
        self.clinic = create_clinic("Clinique Webhook")
        self.payment = SubscriptionPayment.objects.create(clinic=self.clinic, plan_tier="starter",
                                                          billing_cycle="monthly", amount=Decimal("10000"),
                                                          provider_session_id="sess-1")

    def _post(self, body, secret="whsec-test", timestamp=None):
        raw = json.dumps(body).encode()
        timestamp = str(timestamp or int(time.time()))
        signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
        return self.client.post(self.url, raw, content_type="application/json",
                                HTTP_X_WEBHOOK_SIGNATURE=signature, HTTP_X_WEBHOOK_TIMESTAMP=timestamp)

    def test_signed_success_event_activates_after_verification(self):
        with mock.patch("subscriptions.prepaid.saspay.get_session_status",
                        return_value=saspay.SessionStatus(status="PAID", transaction_id="txn-1")), \
             mock.patch("subscriptions.prepaid.saspay.verify_transaction", return_value=_transaction()):
            response = self._post({"event": "transaction.success", "data": {"id": "txn-1"}})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, SubscriptionPayment.Status.PAID)

    def test_bad_signature_or_old_timestamp_is_refused(self):
        self.assertEqual(self._post({"event": "transaction.success", "data": {"id": "txn-1"}}, secret="x").status_code, 400)
        old = int(time.time()) - 3600
        self.assertEqual(self._post({"event": "transaction.success", "data": {"id": "txn-1"}}, timestamp=old).status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, SubscriptionPayment.Status.PENDING)


class PrepaidExpiryTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Échéance")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")

    def _ended(self, days_ago):
        Clinic.objects.filter(pk=self.clinic.pk).update(
            subscription_status="active", current_period_end=timezone.now() - timedelta(days=days_ago)
        )

    def test_past_due_during_grace_then_suspended(self):
        self._ended(1)
        prepaid.expire_prepaid_periods()
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.PAST_DUE)
        Clinic.objects.filter(pk=self.clinic.pk).update(current_period_end=timezone.now() - timedelta(days=4))
        prepaid.expire_prepaid_periods()
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.SUSPENDED)

    def test_writes_allowed_during_grace_and_blocked_after(self):
        payload = {"first_name": "Awa", "last_name": "Grâce", "phone": "770000099", "date_of_birth": "1990-01-01",
                   "gender": "female"}
        self._ended(2)
        self.client.force_authenticate(User.objects.get(pk=self.admin.pk))  # clinique relue à chaque requête
        self.assertEqual(self.client.post(reverse("patient-list"), payload, format="json").status_code, 201)
        self._ended(4)
        self.client.force_authenticate(User.objects.get(pk=self.admin.pk))
        payload["last_name"] = "Après"
        self.assertEqual(self.client.post(reverse("patient-list"), payload, format="json").status_code, 403)
