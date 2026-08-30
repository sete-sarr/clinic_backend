from datetime import date, timedelta

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from patients.models import Patient


class PatientTenantIsolationTests(APITestCase):
    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.secretary_a = create_user(clinic=self.clinic_a, role="secretary")
        self.secretary_b = create_user(clinic=self.clinic_b, role="secretary")
        self.patient_a = Patient.objects.create(
            clinic=self.clinic_a,
            patient_number="PAT-2026-00001",
            first_name="Alice",
            last_name="Anderson",
            phone="0600000001",
            date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.FEMALE,
        )

    def test_clinic_b_cannot_see_clinic_a_patient(self):
        self.client.force_authenticate(self.secretary_b)
        response = self.client.get(reverse("patient-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        patient_ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(self.patient_a.id, patient_ids)

    def test_clinic_b_cannot_retrieve_clinic_a_patient_by_id(self):
        self.client.force_authenticate(self.secretary_b)
        response = self.client.get(reverse("patient-detail", args=[self.patient_a.id]))
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    def test_clinic_a_can_see_own_patient(self):
        self.client.force_authenticate(self.secretary_a)
        response = self.client.get(reverse("patient-detail", args=[self.patient_a.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class PatientPortalAccessTests(APITestCase):
    """Security fix: a patient may read their own record and only their own (docs/known-issues.md)."""

    def setUp(self):
        self.clinic = create_clinic()
        self.patient_a = Patient.objects.create(
            clinic=self.clinic,
            patient_number="PAT-2026-00010",
            first_name="Alice",
            last_name="Anderson",
            phone="0600000010",
            date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.FEMALE,
        )
        self.patient_b = Patient.objects.create(
            clinic=self.clinic,
            patient_number="PAT-2026-00011",
            first_name="Bob",
            last_name="Brown",
            phone="0600000011",
            date_of_birth=date(1991, 1, 1),
            gender=Patient.Gender.MALE,
        )
        self.patient_a_user = create_user(clinic=self.clinic, role="patient")
        self.patient_a.user = self.patient_a_user
        self.patient_a.save()

    def test_patient_list_only_returns_own_record(self):
        self.client.force_authenticate(self.patient_a_user)
        response = self.client.get(reverse("patient-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        patient_ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(patient_ids, [self.patient_a.id])

    def test_patient_cannot_retrieve_another_patients_record(self):
        self.client.force_authenticate(self.patient_a_user)
        response = self.client.get(reverse("patient-detail", args=[self.patient_b.id]))
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    def test_patient_can_retrieve_own_record(self):
        self.client.force_authenticate(self.patient_a_user)
        response = self.client.get(reverse("patient-detail", args=[self.patient_a.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_patient_cannot_write(self):
        self.client.force_authenticate(self.patient_a_user)
        response = self.client.patch(reverse("patient-detail", args=[self.patient_a.id]), {"phone": "0600099999"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PatientBusinessRuleTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.client.force_authenticate(self.secretary)

    def test_cannot_create_patient_with_future_date_of_birth(self):
        payload = {
            "first_name": "Bob",
            "last_name": "Brown",
            "phone": "0600000002",
            "date_of_birth": (date.today() + timedelta(days=1)).isoformat(),
            "gender": "male",
        }
        response = self.client.post(reverse("patient-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patient_number_is_auto_generated(self):
        payload = {
            "first_name": "Carol",
            "last_name": "Clark",
            "phone": "0600000003",
            "date_of_birth": "1985-05-05",
            "gender": "female",
        }
        response = self.client.post(reverse("patient-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["patient_number"].startswith("PAT-"))

    def test_doctor_role_cannot_create_patient(self):
        doctor_user = create_user(clinic=self.clinic, role="doctor")
        self.client.force_authenticate(doctor_user)
        payload = {
            "first_name": "Dan",
            "last_name": "Davis",
            "phone": "0600000004",
            "date_of_birth": "1985-05-05",
            "gender": "male",
        }
        response = self.client.post(reverse("patient-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PatientReportsExportTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.accountant = create_user(clinic=self.clinic, role="accountant")
        self.doctor = create_user(clinic=self.clinic, role="doctor")
        self.patient = Patient.objects.create(
            clinic=self.clinic,
            patient_number="PAT-2026-00020",
            first_name="Eve",
            last_name="Evans",
            phone="0600000020",
            date_of_birth=date(1992, 1, 1),
            gender=Patient.Gender.FEMALE,
        )

    def test_secretary_can_access_statement_pdf(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.get(reverse("patient-statement-pdf", args=[self.patient.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_accountant_can_access_statement_pdf(self):
        self.client.force_authenticate(self.accountant)
        response = self.client.get(reverse("patient-statement-pdf", args=[self.patient.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_doctor_cannot_access_statement_pdf(self):
        self.client.force_authenticate(self.doctor)
        response = self.client.get(reverse("patient-statement-pdf", args=[self.patient.id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_csv_export_returns_csv_with_header(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.get(reverse("patient-export-csv"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode()
        self.assertIn("patient_number", content.splitlines()[0])

    def test_csv_export_records_audit_log(self):
        from common.models import AuditLog

        self.client.force_authenticate(self.secretary)
        self.client.get(reverse("patient-export-csv"))
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.EXPORT, model_name="patient").exists())
