"""Langue des e-mails et SMS (docs/i18n.md §2) : celle du DESTINATAIRE — patients dans la langue de
la clinique, personnel dans la sienne — jamais celle de la requête qui déclenche l'envoi."""

from datetime import date, datetime

from django.test import TestCase
from django.utils import timezone, translation

from appointments.models import Appointment
from appointments.notifications import send_appointment_created_notifications
from common.testing import create_clinic, create_user
from communication.models import NotificationLog, OtpCode
from communication.services import generate_and_send_otp
from doctors.models import Doctor
from patients.models import Patient


def _clinic(name, locale):
    clinic = create_clinic(name)
    clinic.locale = locale
    clinic.phone = "33 800 00 00"
    clinic.save(update_fields=["locale", "phone"])
    return clinic


def _patient(clinic, number="PAT-2026-00100"):
    return Patient.objects.create(
        clinic=clinic, patient_number=number, first_name="Grace", last_name="Hopper", phone="770000000",
        email=f"{number.lower()}@example.com", date_of_birth=date(1990, 1, 1), gender=Patient.Gender.FEMALE,
    )


def _appointment(clinic, patient, doctor_language=""):
    user = create_user(clinic=clinic, role="doctor", email=f"doc-{clinic.pk}@example.com", first_name="Awa", last_name="Diop")
    user.language = doctor_language
    user.save(update_fields=["language"])
    doctor = Doctor.objects.create(user=user, clinic=clinic, professional_number=f"DOC-{user.pk}", specialty="General")
    return Appointment.objects.create(
        clinic=clinic, doctor=doctor, patient=patient, date=date(2026, 10, 1), time=datetime(2026, 10, 1, 9, 30).time(),
        status=Appointment.Status.CONFIRMED,
    )


class AppointmentNotificationLanguageTests(TestCase):
    def test_english_clinic_patient_receives_english_messages(self):
        clinic = _clinic("Sunrise Clinic", "en")
        patient = _patient(clinic)
        appointment = _appointment(clinic, patient)
        send_appointment_created_notifications(appointment_id=appointment.id)

        email = NotificationLog.objects.get(recipient_address=patient.email)
        self.assertEqual(email.subject, "Appointment confirmation — Sunrise Clinic")
        self.assertTrue(email.body.startswith("Hello,"))
        self.assertIn("We confirm your appointment with Dr. Awa Diop on October 1, 2026 at 9:30 AM.", email.body)
        self.assertIn("please contact the clinic on 33 800 00 00", email.body)
        self.assertIn("Kind regards,\nSunrise Clinic", email.body)
        sms = NotificationLog.objects.get(channel=NotificationLog.Channel.SMS)
        self.assertTrue(sms.body.startswith("Sunrise Clinic: your appointment with Dr. Awa Diop is confirmed on October 1, 2026"))

    def test_each_staff_member_gets_their_own_language(self):
        clinic = _clinic("Clinique du Parc", "fr")
        appointment = _appointment(clinic, _patient(clinic), doctor_language="en")
        secretary = create_user(clinic=clinic, role="secretary", email="sec@example.com")
        send_appointment_created_notifications(appointment_id=appointment.id)

        doctor_mail = NotificationLog.objects.get(recipient_address=appointment.doctor.user.email)
        self.assertEqual(doctor_mail.subject, "New appointment — Grace Hopper")
        secretary_mail = NotificationLog.objects.get(recipient_address=secretary.email)
        self.assertEqual(secretary_mail.subject, "Nouveau rendez-vous — Grace Hopper")
        self.assertIn("le 01/10/2026 à 09h30", secretary_mail.body)

    def test_language_of_the_triggering_request_does_not_leak(self):
        clinic = _clinic("Clinique du Parc", "fr")
        patient = _patient(clinic)
        appointment = _appointment(clinic, patient)
        with translation.override("en"):  # la secrétaire qui crée le rendez-vous utilise l'interface en anglais
            send_appointment_created_notifications(appointment_id=appointment.id)
        email = NotificationLog.objects.get(recipient_address=patient.email)
        self.assertEqual(email.subject, "Confirmation de votre rendez-vous — Clinique du Parc")


class OtpLanguageTests(TestCase):
    def test_patient_verification_code_follows_the_clinic_language(self):
        clinic = _clinic("Sunrise Clinic", "en")
        patient = _patient(clinic)
        generate_and_send_otp(principal=patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
        email = NotificationLog.objects.get(channel=NotificationLog.Channel.EMAIL)
        self.assertEqual(email.subject, "Your verification code — Sunrise Clinic")
        self.assertIn("This code is valid for", email.body)
        sms = NotificationLog.objects.get(channel=NotificationLog.Channel.SMS)
        self.assertTrue(sms.body.startswith("Sunrise Clinic: your verification code is"))


class StockAlertLanguageTests(TestCase):
    def test_each_recipient_gets_the_alert_in_their_language(self):
        from pharmacy.models import Medication
        from pharmacy.notifications import send_low_stock_alert

        clinic = _clinic("Clinique du Parc", "fr")
        admin = create_user(clinic=clinic, role="clinic_admin", email="admin@example.com")
        admin.language = "en"
        admin.save(update_fields=["language"])
        create_user(clinic=clinic, role="pharmacist", email="pharma@example.com")
        medication = Medication.objects.create(clinic=clinic, name="Doliprane", unit="boîte", min_threshold=10)
        send_low_stock_alert(medication_id=medication.id)

        self.assertEqual(
            NotificationLog.objects.get(recipient_address="admin@example.com").subject,
            "Low stock alert: Doliprane — Clinique du Parc",
        )
        self.assertEqual(
            NotificationLog.objects.get(recipient_address="pharma@example.com").subject,
            "Alerte stock bas : Doliprane — Clinique du Parc",
        )


class SubscriptionEmailLanguageTests(TestCase):
    def test_expiring_notice_in_the_administrator_language(self):
        from subscriptions.tasks import _notify_expiring

        clinic = _clinic("Sunrise Clinic", "en")
        clinic.current_period_end = timezone.make_aware(datetime(2026, 10, 15, 12, 0))
        clinic.save(update_fields=["current_period_end"])
        create_user(clinic=clinic, role="clinic_admin", email="owner@example.com")
        _notify_expiring(clinic=clinic, days_left=15)

        email = NotificationLog.objects.get(recipient_address="owner@example.com")
        self.assertEqual(email.subject, "Your subscription is due in 15 days — Sunrise Clinic")
        self.assertIn("is due on October 15, 2026", email.body)
        self.assertIn("Kind regards,\nThe proCli team", email.body)

    def test_trial_end_notice_in_french_by_default(self):
        from subscriptions.services import _notify_license_expired

        clinic = _clinic("Clinique du Parc", "fr")
        create_user(clinic=clinic, role="clinic_admin", email="owner@example.com")
        _notify_license_expired(clinic=clinic, trial_ended=True)
        email = NotificationLog.objects.get(recipient_address="owner@example.com")
        self.assertEqual(email.subject, "Fin de votre mois d'essai gratuit — Clinique du Parc")
        self.assertIn("Cordialement,\nL'équipe proCli", email.body)
