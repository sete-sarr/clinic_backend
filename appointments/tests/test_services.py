from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from appointments.models import Appointment
from appointments.notifications import (
    send_appointment_cancelled_notifications,
    send_appointment_created_notifications,
    send_appointment_reminder_notifications,
)
from appointments.services import (
    cancel_appointment_by_patient,
    check_in_appointment,
    create_appointment,
    update_appointment,
    validate_cancellable_by_patient,
)
from common.testing import create_clinic, create_user
from communication.models import NotificationLog
from doctors.models import Doctor
from patients.models import Patient


def _create_doctor(clinic, email=""):
    user = create_user(clinic=clinic, role="doctor", email=email)
    return Doctor.objects.create(user=user, clinic=clinic, professional_number=f"DOC-{user.id}", specialty="General")


def _create_patient(clinic, number, email=""):
    return Patient.objects.create(
        clinic=clinic,
        patient_number=number,
        first_name="Test",
        last_name="Patient",
        phone="0600000000",
        email=email,
        date_of_birth=date(1990, 1, 1),
        gender=Patient.Gender.OTHER,
    )


def _appointment_at(clinic, doctor, patient, dt, status=Appointment.Status.PENDING):
    return Appointment.objects.create(
        clinic=clinic, doctor=doctor, patient=patient, date=dt.date(), time=dt.time(), status=status
    )


class ValidateCancellableByPatientTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00001")

    def test_more_than_24h_out_is_cancellable(self):
        appointment = _appointment_at(
            self.clinic, self.doctor, self.patient, timezone.now() + timedelta(hours=25)
        )
        validate_cancellable_by_patient(appointment=appointment)  # does not raise

    def test_exactly_at_24h_boundary_is_not_cancellable(self):
        appointment = _appointment_at(
            self.clinic, self.doctor, self.patient, timezone.now() + timedelta(hours=24)
        )
        with self.assertRaises(ValidationError):
            validate_cancellable_by_patient(appointment=appointment)

    def test_less_than_24h_out_is_not_cancellable(self):
        appointment = _appointment_at(
            self.clinic, self.doctor, self.patient, timezone.now() + timedelta(hours=1)
        )
        with self.assertRaises(ValidationError):
            validate_cancellable_by_patient(appointment=appointment)

    def test_completed_appointment_is_not_cancellable_even_if_far_out(self):
        appointment = _appointment_at(
            self.clinic,
            self.doctor,
            self.patient,
            timezone.now() + timedelta(days=5),
            status=Appointment.Status.COMPLETED,
        )
        with self.assertRaises(ValidationError):
            validate_cancellable_by_patient(appointment=appointment)

    def test_already_cancelled_appointment_is_not_cancellable(self):
        appointment = _appointment_at(
            self.clinic,
            self.doctor,
            self.patient,
            timezone.now() + timedelta(days=5),
            status=Appointment.Status.CANCELLED,
        )
        with self.assertRaises(ValidationError):
            validate_cancellable_by_patient(appointment=appointment)


class CancelAppointmentByPatientTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00002")

    def test_cancel_sets_status_to_cancelled(self):
        appointment = _appointment_at(
            self.clinic, self.doctor, self.patient, timezone.now() + timedelta(hours=48)
        )
        cancel_appointment_by_patient(appointment=appointment)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.CANCELLED)


class AppointmentNotificationDispatchTests(TestCase):
    """Direct calls to the notification-building functions, following the same shape as
    communication/tests/test_otp.py's OtpServiceTests — no on_commit/Celery involved, since
    send_notification() persists NotificationLog synchronously and only *delivery* is deferred."""

    # patient gets email + SMS (Patient.phone is always set); doctor/secretary only get email
    # (User has no phone field) — so both need an explicit email to be counted here.
    EXPECTED_NOTIFICATION_COUNT = 4  # patient email + patient sms + doctor email + secretary email

    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic, email="doctor@example.com")
        self.patient = _create_patient(self.clinic, "PAT-2026-00004", email="patient@example.com")
        create_user(clinic=self.clinic, role="secretary", email="secretary@example.com")
        self.appointment = _appointment_at(self.clinic, self.doctor, self.patient, timezone.now() + timedelta(days=2))

    def test_send_appointment_created_notifications_dispatches_to_all_recipients(self):
        send_appointment_created_notifications(appointment_id=self.appointment.id)
        logs = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.APPOINTMENT_CREATED)
        self.assertEqual(logs.count(), self.EXPECTED_NOTIFICATION_COUNT)

    def test_send_appointment_cancelled_notifications_dispatches_to_all_recipients(self):
        send_appointment_cancelled_notifications(appointment_id=self.appointment.id)
        logs = NotificationLog.objects.filter(
            notification_type=NotificationLog.NotificationType.APPOINTMENT_CANCELLED
        )
        self.assertEqual(logs.count(), self.EXPECTED_NOTIFICATION_COUNT)

    def test_send_appointment_reminder_notifications_excludes_secretary(self):
        # business/notification-rules.md RAPPEL DE RENDEZ-VOUS: Patient, Médecin only — unlike
        # created/cancelled, no Receptionist row.
        send_appointment_reminder_notifications(appointment_id=self.appointment.id)
        logs = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.APPOINTMENT_REMINDER)
        self.assertEqual(logs.count(), 3)  # patient email + patient sms + doctor email
        self.assertFalse(logs.filter(recipient_address="secretary@example.com").exists())

    def test_unknown_appointment_id_is_a_silent_no_op(self):
        send_appointment_created_notifications(appointment_id=999999)
        self.assertEqual(NotificationLog.objects.count(), 0)


class SendDayBeforeRemindersTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic, email="doctor@example.com")
        self.patient = _create_patient(self.clinic, "PAT-2026-00008", email="patient@example.com")

    def _tomorrow_appointment(self, status=Appointment.Status.CONFIRMED):
        tomorrow = timezone.localdate() + timedelta(days=1)
        return Appointment.objects.create(
            clinic=self.clinic, doctor=self.doctor, patient=self.patient,
            date=tomorrow, time="09:00:00", status=status,
        )

    def test_dispatches_and_marks_reminder_sent(self):
        from appointments.tasks import send_day_before_reminders

        appointment = self._tomorrow_appointment()
        send_day_before_reminders()
        appointment.refresh_from_db()
        self.assertIsNotNone(appointment.day_before_reminder_sent_at)
        logs = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.APPOINTMENT_REMINDER)
        self.assertEqual(logs.count(), 3)

    def test_is_idempotent_once_already_sent(self):
        from appointments.tasks import send_day_before_reminders

        appointment = self._tomorrow_appointment()
        appointment.day_before_reminder_sent_at = timezone.now()
        appointment.save(update_fields=["day_before_reminder_sent_at"])
        send_day_before_reminders()
        logs = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.APPOINTMENT_REMINDER)
        self.assertEqual(logs.count(), 0)

    def test_ignores_appointments_not_tomorrow(self):
        from appointments.tasks import send_day_before_reminders

        Appointment.objects.create(
            clinic=self.clinic, doctor=self.doctor, patient=self.patient,
            date=timezone.localdate() + timedelta(days=2), time="09:00:00",
            status=Appointment.Status.CONFIRMED,
        )
        send_day_before_reminders()
        logs = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.APPOINTMENT_REMINDER)
        self.assertEqual(logs.count(), 0)

    def test_ignores_cancelled_appointments(self):
        from appointments.tasks import send_day_before_reminders

        self._tomorrow_appointment(status=Appointment.Status.CANCELLED)
        send_day_before_reminders()
        logs = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.APPOINTMENT_REMINDER)
        self.assertEqual(logs.count(), 0)


class NotificationWiringTests(TestCase):
    """Verifies create_appointment/update_appointment/cancel_appointment_by_patient correctly
    schedule the notification dispatch via transaction.on_commit — mocks the dispatch functions
    (and the existing reminder Celery task) so nothing tries to reach a real Celery broker."""

    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00005")

    def test_create_appointment_schedules_created_notification(self):
        with patch("appointments.notifications.send_appointment_created_notifications") as mock_notify:
            with self.captureOnCommitCallbacks(execute=True):
                appointment = create_appointment(
                    clinic=self.clinic,
                    doctor=self.doctor,
                    patient=self.patient,
                    date=(timezone.now() + timedelta(days=2)).date(),
                    time="09:00:00",
                )
        mock_notify.assert_called_once_with(appointment_id=appointment.id)

    def test_update_appointment_date_change_schedules_modified_notification(self):
        appointment = _appointment_at(self.clinic, self.doctor, self.patient, timezone.now() + timedelta(hours=48))
        with patch("appointments.notifications.send_appointment_modified_notifications") as mock_notify:
            with self.captureOnCommitCallbacks(execute=True):
                update_appointment(appointment=appointment, time="10:00:00")
        mock_notify.assert_called_once_with(appointment_id=appointment.id)

    def test_cancel_appointment_by_patient_schedules_cancelled_notification(self):
        appointment = _appointment_at(self.clinic, self.doctor, self.patient, timezone.now() + timedelta(hours=48))
        with patch("appointments.notifications.send_appointment_cancelled_notifications") as mock_notify:
            with self.captureOnCommitCallbacks(execute=True):
                cancel_appointment_by_patient(appointment=appointment)
        mock_notify.assert_called_once_with(appointment_id=appointment.id)

    def test_update_appointment_to_cancelled_schedules_cancelled_notification(self):
        appointment = _appointment_at(self.clinic, self.doctor, self.patient, timezone.now() + timedelta(hours=48))
        with patch("appointments.notifications.send_appointment_cancelled_notifications") as mock_notify:
            with self.captureOnCommitCallbacks(execute=True):
                update_appointment(appointment=appointment, status=Appointment.Status.CANCELLED)
        mock_notify.assert_called_once_with(appointment_id=appointment.id)

    def test_update_appointment_without_cancel_does_not_schedule_notification(self):
        appointment = _appointment_at(self.clinic, self.doctor, self.patient, timezone.now() + timedelta(hours=48))
        with patch("appointments.notifications.send_appointment_cancelled_notifications") as mock_notify:
            with self.captureOnCommitCallbacks(execute=True):
                update_appointment(appointment=appointment, reason="Follow-up")
        mock_notify.assert_not_called()


class CheckInAppointmentTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00006")

    def test_success_sets_checked_in_at_and_ticket_number(self):
        appointment = _appointment_at(self.clinic, self.doctor, self.patient, timezone.now())
        check_in_appointment(appointment=appointment)
        appointment.refresh_from_db()
        self.assertIsNotNone(appointment.checked_in_at)
        year = timezone.localdate().year
        self.assertEqual(appointment.ticket_number, f"CHK-{year}-00001")

    def test_ticket_numbers_increment_per_clinic_per_year(self):
        appointment_a = _appointment_at(self.clinic, self.doctor, self.patient, timezone.now())
        other_patient = _create_patient(self.clinic, "PAT-2026-00007")
        appointment_b = _appointment_at(self.clinic, self.doctor, other_patient, timezone.now())
        check_in_appointment(appointment=appointment_a)
        check_in_appointment(appointment=appointment_b)
        appointment_a.refresh_from_db()
        appointment_b.refresh_from_db()
        year = timezone.localdate().year
        self.assertEqual(appointment_a.ticket_number, f"CHK-{year}-00001")
        self.assertEqual(appointment_b.ticket_number, f"CHK-{year}-00002")

    def test_rejects_non_today_appointment(self):
        appointment = _appointment_at(self.clinic, self.doctor, self.patient, timezone.now() + timedelta(days=1))
        with self.assertRaises(ValidationError):
            check_in_appointment(appointment=appointment)

    def test_rejects_terminal_status(self):
        appointment = _appointment_at(
            self.clinic, self.doctor, self.patient, timezone.now(), status=Appointment.Status.COMPLETED
        )
        with self.assertRaises(ValidationError):
            check_in_appointment(appointment=appointment)

    def test_rejects_double_check_in(self):
        appointment = _appointment_at(self.clinic, self.doctor, self.patient, timezone.now())
        check_in_appointment(appointment=appointment)
        appointment.refresh_from_db()
        with self.assertRaises(ValidationError):
            check_in_appointment(appointment=appointment)
