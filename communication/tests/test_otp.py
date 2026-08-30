import re
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from common.testing import create_clinic
from communication.models import NotificationLog, OtpCode
from communication.services import generate_and_send_otp, verify_otp
from patients.models import Patient


def _create_patient(clinic, **overrides):
    defaults = dict(
        clinic=clinic,
        patient_number="PAT-2026-00001",
        first_name="Test",
        last_name="Patient",
        phone="0600000000",
        email="patient@example.com",
        date_of_birth=date(1990, 1, 1),
        gender=Patient.Gender.OTHER,
    )
    defaults.update(overrides)
    return Patient.objects.create(**defaults)


class OtpServiceTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.patient = _create_patient(self.clinic)

    def test_generate_and_send_otp_creates_code_and_notifications(self):
        otp = generate_and_send_otp(principal=self.patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
        self.assertEqual(otp.patient, self.patient)
        self.assertIsNone(otp.user)
        self.assertEqual(NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.OTP).count(), 2)

    def test_cooldown_blocks_immediate_resend(self):
        generate_and_send_otp(principal=self.patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
        with self.assertRaises(ValidationError):
            generate_and_send_otp(principal=self.patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)

    def test_verify_otp_succeeds_with_correct_code_and_consumes_it(self):
        generate_and_send_otp(principal=self.patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
        log = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.OTP).first()
        code = re.search(r"\d{6}", log.body).group()

        verify_otp(principal=self.patient, code=code, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)

        otp = OtpCode.objects.get(patient=self.patient)
        self.assertIsNotNone(otp.consumed_at)
        with self.assertRaises(ValidationError):
            verify_otp(principal=self.patient, code=code, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)

    def test_verify_otp_wrong_code_increments_attempts(self):
        generate_and_send_otp(principal=self.patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
        with self.assertRaises(ValidationError):
            verify_otp(principal=self.patient, code="000000", purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
        otp = OtpCode.objects.get(patient=self.patient)
        self.assertEqual(otp.attempts, 1)
        self.assertIsNone(otp.consumed_at)

    def test_verify_otp_locks_after_max_attempts(self):
        generate_and_send_otp(principal=self.patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
        for _ in range(OtpCode.MAX_ATTEMPTS):
            with self.assertRaises(ValidationError):
                verify_otp(principal=self.patient, code="000000", purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
        otp = OtpCode.objects.get(patient=self.patient)
        self.assertIsNotNone(otp.consumed_at)
        # Even the (hypothetically) correct code no longer works once consumed.
        with self.assertRaises(ValidationError):
            verify_otp(principal=self.patient, code="000000", purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)

    def test_verify_otp_expired_code_rejected(self):
        otp = generate_and_send_otp(principal=self.patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
        otp.expires_at = timezone.now() - timedelta(seconds=1)
        otp.save(update_fields=["expires_at"])
        with self.assertRaises(ValidationError):
            verify_otp(principal=self.patient, code="anything", purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
