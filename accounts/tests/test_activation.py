import re
from datetime import date

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic
from communication.models import NotificationLog
from patients.models import Patient


class PatientActivationTests(APITestCase):
    def setUp(self):
        cache.clear()  # ScopedRateThrottle uses the default (process-shared) cache.
        self.clinic = create_clinic()
        self.patient = Patient.objects.create(
            clinic=self.clinic,
            patient_number="PAT-2026-00001",
            first_name="Test",
            last_name="Patient",
            phone="0600000000",
            email="patient@example.com",
            date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.identity = {
            "clinic": self.clinic.id,
            "patient_number": self.patient.patient_number,
            "phone": self.patient.phone,
            "date_of_birth": "1990-01-01",
        }

    def _latest_otp_code(self):
        log = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.OTP).latest(
            "created_at"
        )
        return re.search(r"\d{6}", log.body).group()

    def test_request_activation_matching_identity_sends_otp(self):
        response = self.client.post(reverse("patient-activation-request"), self.identity)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.OTP).exists())

    def test_request_activation_non_matching_identity_still_returns_generic_success(self):
        payload = {**self.identity, "phone": "0699999999"}
        response = self.client.post(reverse("patient-activation-request"), payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(NotificationLog.objects.exists())

    def test_full_activation_flow_happy_path(self):
        self.client.post(reverse("patient-activation-request"), self.identity)
        code = self._latest_otp_code()

        verify_payload = {
            **self.identity,
            "code": code,
            "username": "test.patient",
            "password": "S3cure!Passw0rd",
        }
        response = self.client.post(reverse("patient-activation-verify"), verify_payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.patient.refresh_from_db()
        self.assertIsNotNone(self.patient.user)
        self.assertTrue(self.patient.user.groups.filter(name="patient").exists())
        self.assertTrue(self.patient.user.check_password("S3cure!Passw0rd"))

    def test_verify_with_wrong_code_fails(self):
        self.client.post(reverse("patient-activation-request"), self.identity)
        verify_payload = {
            **self.identity,
            "code": "000000",
            "username": "test.patient",
            "password": "S3cure!Passw0rd",
        }
        response = self.client.post(reverse("patient-activation-verify"), verify_payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.patient.refresh_from_db()
        self.assertIsNone(self.patient.user)

    def test_already_activated_patient_cannot_reactivate(self):
        self.client.post(reverse("patient-activation-request"), self.identity)
        code = self._latest_otp_code()
        first_verify = {**self.identity, "code": code, "username": "test.patient", "password": "S3cure!Passw0rd"}
        self.client.post(reverse("patient-activation-verify"), first_verify)

        # A second request against the same (now-linked) patient must not match anymore.
        response = self.client.post(reverse("patient-activation-request"), self.identity)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.OTP).count(), 2
        )  # only the first request's two notifications (email+sms), none from the second

    def test_throttle_trips_after_five_requests_per_hour(self):
        for _ in range(5):
            response = self.client.post(reverse("patient-activation-request"), self.identity)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.post(reverse("patient-activation-request"), self.identity)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
