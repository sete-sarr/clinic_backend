"""Essai de 14 jours, suspension en lecture seule, clinique de démonstration, modules réservés à
Professional et interrupteur SMS (décisions du 2026-10-14)."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from clinics.models import Clinic
from common.testing import create_clinic, create_user
from communication.models import NotificationLog
from communication.services import send_notification
from patients.models import Patient
from subscriptions.catalog import APPOINTMENT_SMS, PHARMACY, has_feature, visible_modules
from subscriptions.services import expire_trials


def _patient_payload(n):
    return {"first_name": "Awa", "last_name": f"Test{n}", "phone": f"7700000{n:02d}",
            "date_of_birth": str(date(1990, 1, 1)), "gender": "female"}


class TrialEndTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Essai")
        self.clinic.trial_ends_at = timezone.now() - timedelta(hours=1)
        self.clinic.save()
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.client.force_authenticate(self.admin)

    def test_expired_trial_is_read_only_even_before_the_daily_task(self):
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.TRIAL)
        self.assertEqual(self.client.get(reverse("patient-list")).status_code, status.HTTP_200_OK)
        response = self.client.post(reverse("patient-list"), _patient_payload(1), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_daily_task_suspends_but_spares_the_demo_clinic(self):
        demo = create_clinic("Clinique Démo")
        demo.trial_ends_at = timezone.now() - timedelta(days=3)
        demo.is_demo = True
        demo.save()
        self.assertEqual(expire_trials(), 1)
        self.clinic.refresh_from_db()
        demo.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.SUSPENDED)
        self.assertEqual(demo.subscription_status, Clinic.SubscriptionStatus.TRIAL)
        self.client.force_authenticate(create_user(clinic=demo, role="clinic_admin"))
        response = self.client.post(reverse("patient-list"), _patient_payload(2), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)


class ProfessionalModulesTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Starter")
        self.clinic.subscription_status = Clinic.SubscriptionStatus.ACTIVE
        self.clinic.plan_tier = Clinic.PlanTier.STARTER
        self.clinic.save()
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.client.force_authenticate(self.admin)

    def _create_medication(self):
        return self.client.post(reverse("medication-list"), {"name": "Paracétamol", "unit": "comprimé"}, format="json")

    def test_starter_cannot_write_in_professional_modules_but_keeps_reading(self):
        response = self._create_medication()
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("Professional", response.data["message"])
        self.assertEqual(self.client.get(reverse("medication-list")).status_code, status.HTTP_200_OK)
        self.assertEqual(visible_modules(self.clinic), [])
        self.assertEqual(self.client.get(reverse("me")).data["clinic_modules"], [])

    def test_professional_and_trial_have_every_module(self):
        self.clinic.plan_tier = Clinic.PlanTier.PROFESSIONAL
        self.clinic.save()
        self.assertEqual(self._create_medication().status_code, status.HTTP_201_CREATED)
        trial = create_clinic("Clinique En Essai")
        self.assertTrue(has_feature(trial, PHARMACY))
        self.assertEqual(visible_modules(trial), ["hospitalization", "laboratory", "pharmacy"])

    def test_suspended_clinic_still_sees_its_modules_read_only(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic.save()
        self.assertEqual(visible_modules(self.clinic), ["hospitalization", "laboratory", "pharmacy"])


class SmsSwitchTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique SMS")
        self.clinic.plan_tier = Clinic.PlanTier.PROFESSIONAL
        self.clinic.save()

    def _sms(self):
        return send_notification(
            clinic=self.clinic, recipient_user=None, channel=NotificationLog.Channel.SMS,
            notification_type=NotificationLog.NotificationType.APPOINTMENT_CREATED,
            recipient_address="770000000", subject="", body="Test",
        )

    @override_settings(SMS_ENABLED=False)
    def test_disabled_sms_are_neither_prepared_nor_offered(self):
        self.assertIsNone(self._sms())
        self.assertFalse(NotificationLog.objects.exists())
        self.assertFalse(has_feature(self.clinic, APPOINTMENT_SMS))

    @override_settings(SMS_ENABLED=True)
    def test_enabled_sms_are_sent(self):
        with self.captureOnCommitCallbacks(execute=True):
            log = self._sms()
        log.refresh_from_db()
        self.assertNotEqual(log.status, NotificationLog.Status.PENDING)
        self.assertTrue(has_feature(self.clinic, APPOINTMENT_SMS))


class EmailDeliveryTests(TestCase):
    def test_email_is_sent_without_a_celery_worker(self):
        clinic = create_clinic("Clinique Mail")
        with self.captureOnCommitCallbacks(execute=True):
            log = send_notification(
                clinic=clinic, recipient_user=None, channel=NotificationLog.Channel.EMAIL,
                notification_type=NotificationLog.NotificationType.APPOINTMENT_CREATED,
                recipient_address="patient@example.com", subject="Rendez-vous", body="Bonjour",
            )
        log.refresh_from_db()
        self.assertEqual(log.status, NotificationLog.Status.SENT)


@override_settings(CRON_SECRET="secret-de-test")
class DailyTasksEndpointTests(APITestCase):
    url = "/api/v1/internal/daily-tasks/"

    def test_runs_every_task_with_the_shared_secret(self):
        clinic = create_clinic("Clinique Cron")
        clinic.trial_ends_at = timezone.now() - timedelta(days=1)
        clinic.save()
        response = self.client.post(self.url, HTTP_AUTHORIZATION="Bearer secret-de-test")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["expire_ended_trials"], {"ok": True, "result": 1})
        clinic.refresh_from_db()
        self.assertEqual(clinic.subscription_status, Clinic.SubscriptionStatus.SUSPENDED)

    def test_refuses_a_wrong_or_missing_secret(self):
        self.assertEqual(self.client.post(self.url).status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.post(self.url, HTTP_AUTHORIZATION="Bearer mauvais")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(CRON_SECRET="")
    def test_disabled_without_a_configured_secret(self):
        self.assertEqual(self.client.post(self.url).status_code, status.HTTP_404_NOT_FOUND)
