"""Commande seed_demo_clinic : clinique de démonstration complète, sans e-mail ni SMS, jamais en double."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from appointments.models import Appointment
from clinics.models import Clinic
from communication.models import NotificationLog
from consultations.models import Consultation
from hospitalization.models import Admission
from laboratory.models import LabOrder


class SeedDemoClinicTests(TestCase):
    def test_creates_a_coherent_clinic_once_and_sends_nothing(self):
        out = StringIO()
        call_command("seed_demo_clinic", stdout=out)
        clinic = Clinic.objects.get(name="Clinique Teranga Santé")
        self.assertIn("direction@teranga-sante.example", out.getvalue())
        self.assertTrue(Consultation.objects.filter(clinic=clinic, status=Consultation.Status.VALIDATED).exists())
        self.assertEqual(Appointment.objects.filter(clinic=clinic, doctor__isnull=True).count(), 3)
        self.assertTrue(LabOrder.objects.filter(clinic=clinic, status=LabOrder.Status.VALIDATED).exists())
        self.assertEqual(Admission.objects.filter(clinic=clinic, status=Admission.Status.ADMITTED).count(), 2)
        self.assertFalse(NotificationLog.objects.filter(clinic=clinic).exists())

        with self.assertRaises(CommandError):
            call_command("seed_demo_clinic", stdout=StringIO())
        self.assertEqual(Clinic.objects.filter(name="Clinique Teranga Santé").count(), 1)
