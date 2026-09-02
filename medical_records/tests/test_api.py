from datetime import date

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from doctors.models import Doctor
from medical_records.models import MedicalRecord
from patients.models import Patient


class MedicalRecordAccessTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        doctor_user = create_user(clinic=self.clinic, role="doctor")
        self.doctor = Doctor.objects.create(
            user=doctor_user, clinic=self.clinic, professional_number="DOC-001", specialty="General"
        )
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        patient_user = create_user(clinic=self.clinic, role="patient")
        self.patient = Patient.objects.create(
            clinic=self.clinic,
            user=patient_user,
            patient_number="PAT-2026-00001",
            first_name="Test",
            last_name="Patient",
            phone="0600000000",
            date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.record = MedicalRecord.objects.create(clinic=self.clinic, patient=self.patient, allergies="Penicillin")

        self.other_patient = Patient.objects.create(
            clinic=self.clinic,
            patient_number="PAT-2026-00002",
            first_name="Other",
            last_name="Patient",
            phone="0600000002",
            date_of_birth=date(1985, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.other_record = MedicalRecord.objects.create(
            clinic=self.clinic, patient=self.other_patient, allergies="Peanuts"
        )

    def test_patient_list_only_returns_own_record(self):
        """Security fix: CanAccessMedicalRecord grants patients SAFE_METHODS, so without queryset
        scoping the list endpoint used to return every patient's medical record (docs/known-issues.md)."""
        self.client.force_authenticate(self.patient.user)
        response = self.client.get(reverse("medical-record-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        record_ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(record_ids, [self.record.id])

    def test_secretary_cannot_read_medical_record(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.get(reverse("medical-record-detail", args=[self.record.id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_doctor_can_read_and_update(self):
        self.client.force_authenticate(self.doctor.user)
        response = self.client.patch(
            reverse("medical-record-detail", args=[self.record.id]), {"observations": "Stable"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_patient_can_read_own_record_only(self):
        self.client.force_authenticate(self.patient.user)
        response = self.client.get(reverse("medical-record-detail", args=[self.record.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_patient_cannot_update_own_record(self):
        self.client.force_authenticate(self.patient.user)
        response = self.client.patch(
            reverse("medical-record-detail", args=[self.record.id]), {"observations": "Self-edited"}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_search_by_patient_first_name_filters_results(self):
        self.client.force_authenticate(self.doctor.user)
        response = self.client.get(reverse("medical-record-list"), {"search": self.patient.first_name})
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.record.id, ids)
        self.assertNotIn(self.other_record.id, ids)

    def test_search_by_patient_number_filters_results(self):
        self.client.force_authenticate(self.doctor.user)
        response = self.client.get(reverse("medical-record-list"), {"search": self.patient.patient_number})
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.record.id, ids)
        self.assertNotIn(self.other_record.id, ids)
