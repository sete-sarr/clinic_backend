from datetime import date
from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from doctors.models import Doctor
from patients.models import Patient


def _create_patient(clinic, number="PAT-2026-00001"):
    return Patient.objects.create(
        clinic=clinic,
        patient_number=number,
        first_name="Test",
        last_name="Patient",
        phone="0600000000",
        date_of_birth=date(1990, 1, 1),
        gender=Patient.Gender.OTHER,
    )


class InvoiceCreationTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.accountant = create_user(clinic=self.clinic, role="accountant")
        self.patient = _create_patient(self.clinic)
        self.client.force_authenticate(self.accountant)

    def test_vat_and_total_are_persisted_on_creation(self):
        payload = {
            "patient": self.patient.id,
            "issue_date": date.today().isoformat(),
            "vat_rate": "0.18",
            "lines": [{"description": "Consultation", "quantity": 1, "unit_price": "100.00"}],
        }
        response = self.client.post(reverse("invoice-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data["subtotal"]), Decimal("100.00"))
        self.assertEqual(Decimal(response.data["vat_amount"]), Decimal("18.00"))
        self.assertEqual(Decimal(response.data["total_amount"]), Decimal("118.00"))
        self.assertTrue(response.data["number"].startswith("INV-"))

    def test_invoice_requires_at_least_one_line(self):
        payload = {
            "patient": self.patient.id,
            "issue_date": date.today().isoformat(),
            "lines": [],
        }
        response = self.client.post(reverse("invoice-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_secretary_cannot_see_other_clinics_invoices(self):
        other_clinic = create_clinic("Other Clinic")
        other_secretary = create_user(clinic=other_clinic, role="secretary")
        self.client.force_authenticate(self.accountant)
        payload = {
            "patient": self.patient.id,
            "issue_date": date.today().isoformat(),
            "lines": [{"description": "X-ray", "quantity": 1, "unit_price": "50.00"}],
        }
        created = self.client.post(reverse("invoice-list"), payload, format="json")
        self.client.force_authenticate(other_secretary)
        response = self.client.get(reverse("invoice-list"))
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(created.data["id"], ids)


class InvoiceCsvExportTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.accountant = create_user(clinic=self.clinic, role="accountant")
        self.patient = _create_patient(self.clinic, "PAT-2026-00002")
        self.client.force_authenticate(self.accountant)
        payload = {
            "patient": self.patient.id,
            "issue_date": date.today().isoformat(),
            "vat_rate": "0.18",
            "lines": [{"description": "Consultation", "quantity": 1, "unit_price": "100.00"}],
        }
        self.client.post(reverse("invoice-list"), payload, format="json")

    def test_csv_export_returns_csv_with_header(self):
        response = self.client.get(reverse("invoice-export-csv"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode()
        self.assertIn("total_amount", content.splitlines()[0])

    def test_csv_export_records_audit_log(self):
        from common.models import AuditLog

        self.client.get(reverse("invoice-export-csv"))
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.EXPORT, model_name="invoice").exists())


class InvoiceCrossClinicFKTests(APITestCase):
    """Regression coverage for the cross-tenant FK injection audit finding: staff must not be able
    to create/update an invoice referencing another clinic's patient or doctor."""

    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.accountant_a = create_user(clinic=self.clinic_a, role="accountant")
        self.patient_a = _create_patient(self.clinic_a, "PAT-2026-00003")
        self.patient_b = _create_patient(self.clinic_b, "PAT-2026-00004")
        doctor_user_b = create_user(clinic=self.clinic_b, role="doctor")
        self.doctor_b = Doctor.objects.create(
            user=doctor_user_b, clinic=self.clinic_b, professional_number="DOC-B01", specialty="General"
        )
        self.client.force_authenticate(self.accountant_a)

    def test_cannot_create_invoice_with_other_clinics_patient(self):
        payload = {
            "patient": self.patient_b.id,
            "issue_date": date.today().isoformat(),
            "lines": [{"description": "Consultation", "quantity": 1, "unit_price": "100.00"}],
        }
        response = self.client.post(reverse("invoice-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_create_invoice_with_other_clinics_doctor(self):
        payload = {
            "patient": self.patient_a.id,
            "doctor": self.doctor_b.id,
            "issue_date": date.today().isoformat(),
            "lines": [{"description": "Consultation", "quantity": 1, "unit_price": "100.00"}],
        }
        response = self.client.post(reverse("invoice-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_update_invoice_to_other_clinics_patient(self):
        payload = {
            "patient": self.patient_a.id,
            "issue_date": date.today().isoformat(),
            "lines": [{"description": "Consultation", "quantity": 1, "unit_price": "100.00"}],
        }
        created = self.client.post(reverse("invoice-list"), payload, format="json")
        response = self.client.patch(
            reverse("invoice-detail", args=[created.data["id"]]), {"patient": self.patient_b.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
