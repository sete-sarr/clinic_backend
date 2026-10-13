"""Le médecin ne voit que ses patients (patients/access.py, business/access-policy.md), sauf accès
d'urgence motivé, journalisé et limité à 24 h."""

from datetime import date, time, timedelta
from decimal import Decimal
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from appointments.models import Appointment
from billing.services import create_invoice
from common.models import AuditLog
from common.testing import create_clinic, create_user
from communication.models import InAppNotification
from consultations.models import Consultation
from doctors.models import Doctor
from medical_records.models import MedicalRecord
from patients.models import EmergencyAccess, Patient


def _doctor(clinic, name):
    user = create_user(clinic=clinic, role="doctor", first_name=name)
    return Doctor.objects.create(user=user, clinic=clinic, professional_number=f"D-{name}", specialty="General")


def _patient(clinic, number):
    return Patient.objects.create(
        clinic=clinic, patient_number=number, first_name="P", last_name=number, phone="0600000000",
        date_of_birth=date(1990, 1, 1), gender=Patient.Gender.OTHER,
    )


class DoctorOwnPatientsTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Accès")
        self.doctor_a = _doctor(self.clinic, "Alpha")
        self.doctor_b = _doctor(self.clinic, "Beta")
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.mine = _patient(self.clinic, "PAT-A")
        self.theirs = _patient(self.clinic, "PAT-B")
        Appointment.objects.create(
            clinic=self.clinic, patient=self.mine, doctor=self.doctor_a,
            date=date.today() + timedelta(days=1), time=time(9, 0),
        )
        Consultation.objects.create(clinic=self.clinic, patient=self.theirs, doctor=self.doctor_b, date=timezone.now())
        MedicalRecord.objects.get_or_create(clinic=self.clinic, patient=self.mine)
        MedicalRecord.objects.get_or_create(clinic=self.clinic, patient=self.theirs)
        self.mine_invoice = create_invoice(
            clinic=self.clinic, patient=self.mine,
            lines=[{"description": "Consultation", "quantity": 1, "unit_price": Decimal("10")}],
        )
        self.theirs_invoice = create_invoice(
            clinic=self.clinic, patient=self.theirs,
            lines=[{"description": "Consultation", "quantity": 1, "unit_price": Decimal("10")}],
        )

    def _ids(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return {item["id"] for item in response.data["results"]}

    def test_doctor_sees_only_own_patients_records_and_invoices(self):
        self.client.force_authenticate(self.doctor_a.user)
        self.assertEqual(self._ids(reverse("patient-list")), {self.mine.id})
        records = self.client.get(reverse("medical-record-list")).data["results"]
        self.assertEqual({record["patient"] for record in records}, {self.mine.id})
        self.assertEqual(self._ids(reverse("invoice-list")), {self.mine_invoice.id})
        self.assertEqual(
            self.client.get(reverse("patient-detail", args=[self.theirs.id])).status_code, status.HTTP_404_NOT_FOUND
        )

    def test_staff_roles_still_see_the_whole_clinic(self):
        for user in (self.secretary, self.admin):
            self.client.force_authenticate(user)
            self.assertEqual(self._ids(reverse("patient-list")), {self.mine.id, self.theirs.id})

    def test_global_search_does_not_reveal_other_patients(self):
        self.client.force_authenticate(self.doctor_a.user)
        results = self.client.get(reverse("global-search"), {"q": "PAT-"}).data["patients"]
        self.assertEqual({item["id"] for item in results}, {self.mine.id})

    def test_doctor_cannot_create_acts_for_someone_elses_patient(self):
        self.client.force_authenticate(self.doctor_a.user)
        response = self.client.post(
            reverse("consultation-list"),
            {"patient": self.theirs.id, "doctor": self.doctor_a.id, "date": timezone.now().isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.post(
            reverse("appointment-list"),
            {"patient": self.theirs.id, "doctor": self.doctor_a.id, "date": str(date.today() + timedelta(days=3)),
             "time": "10:00"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class EmergencyAccessTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Urgence")
        self.doctor = _doctor(self.clinic, "Gamma")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.patient = _patient(self.clinic, "PAT-U")
        self.url = reverse("patient-emergency-access")
        self.client.force_authenticate(self.doctor.user)

    def _grant(self, number="PAT-U", reason="Patient inconscient aux urgences"):
        return self.client.post(self.url, {"patient_number": number, "reason": reason}, format="json")

    def test_emergency_access_opens_the_record_for_24_hours_and_is_traced(self):
        self.assertEqual(self.client.get(reverse("patient-detail", args=[self.patient.id])).status_code, 404)
        with self.captureOnCommitCallbacks(execute=True):
            response = self._grant()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["id"], self.patient.id)
        self.assertEqual(self.client.get(reverse("patient-detail", args=[self.patient.id])).status_code, 200)

        log = AuditLog.objects.get(action=AuditLog.Action.EMERGENCY_ACCESS)
        self.assertEqual((log.user, log.object_id), (self.doctor.user, str(self.patient.id)))
        self.assertEqual(log.metadata["reason"], "Patient inconscient aux urgences")
        notification = InAppNotification.objects.get(recipient=self.admin)
        self.assertIn("PAT-U", notification.body)
        self.assertNotIn("inconscient", notification.body)

        with mock.patch("patients.access.timezone.now", return_value=timezone.now() + timedelta(hours=25)):
            self.assertEqual(self.client.get(reverse("patient-detail", args=[self.patient.id])).status_code, 404)

    def test_reason_and_exact_number_are_required(self):
        self.assertEqual(self._grant(reason="urgence").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._grant(number="PAT").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(EmergencyAccess.objects.exists())

    def test_only_doctors_and_only_in_their_clinic(self):
        self.client.force_authenticate(create_user(clinic=self.clinic, role="secretary"))
        self.assertEqual(self._grant().status_code, status.HTTP_403_FORBIDDEN)
        other = _doctor(create_clinic("Autre"), "Delta")
        self.client.force_authenticate(other.user)
        self.assertEqual(self._grant().status_code, status.HTTP_400_BAD_REQUEST)
