from datetime import date

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import Invoice, InvoiceLine
from common.testing import create_clinic, create_user
from patients.models import Patient
from payments.models import Payment


class PaymentBusinessRuleTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.accountant = create_user(clinic=self.clinic, role="accountant")
        self.patient = Patient.objects.create(
            clinic=self.clinic,
            patient_number="PAT-2026-00001",
            first_name="Test",
            last_name="Patient",
            phone="0600000000",
            date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.invoice = Invoice.objects.create(
            clinic=self.clinic,
            patient=self.patient,
            number="INV-2026-00001",
            issue_date=date.today(),
            subtotal="100.00",
            vat_rate="0.18",
            vat_amount="18.00",
            total_amount="118.00",
            status=Invoice.Status.ISSUED,
        )
        InvoiceLine.objects.create(
            invoice=self.invoice, description="Consultation", quantity=1, unit_price="100.00", line_total="100.00"
        )
        self.client.force_authenticate(self.accountant)

    def test_overpayment_is_rejected(self):
        payload = {"invoice": self.invoice.id, "amount": "200.00", "method": "cash", "date": date.today().isoformat()}
        response = self.client.post(reverse("payment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_full_payment_marks_invoice_as_paid(self):
        payload = {"invoice": self.invoice.id, "amount": "118.00", "method": "cash", "date": date.today().isoformat()}
        response = self.client.post(reverse("payment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)

    def test_secretary_cannot_record_payment(self):
        secretary = create_user(clinic=self.clinic, role="secretary")
        self.client.force_authenticate(secretary)
        payload = {"invoice": self.invoice.id, "amount": "50.00", "method": "cash", "date": date.today().isoformat()}
        response = self.client.post(reverse("payment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PaymentReportsExportTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.accountant = create_user(clinic=self.clinic, role="accountant")
        self.patient = Patient.objects.create(
            clinic=self.clinic,
            patient_number="PAT-2026-00002",
            first_name="Test",
            last_name="Patient",
            phone="0600000001",
            date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.invoice = Invoice.objects.create(
            clinic=self.clinic,
            patient=self.patient,
            number="INV-2026-00002",
            issue_date=date.today(),
            subtotal="100.00",
            vat_rate="0.18",
            vat_amount="18.00",
            total_amount="118.00",
            status=Invoice.Status.ISSUED,
        )
        self.client.force_authenticate(self.accountant)
        payload = {"invoice": self.invoice.id, "amount": "118.00", "method": "cash", "date": date.today().isoformat()}
        self.payment_id = self.client.post(reverse("payment-list"), payload).data["id"]

    def test_receipt_pdf_returns_pdf(self):
        response = self.client.get(reverse("payment-pdf", args=[self.payment_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_doctor_cannot_access_receipt_pdf_for_unrelated_patient(self):
        from doctors.models import Doctor

        doctor_user = create_user(clinic=self.clinic, role="doctor")
        Doctor.objects.create(user=doctor_user, clinic=self.clinic, professional_number="DOC-1", specialty="General")
        self.client.force_authenticate(doctor_user)
        response = self.client.get(reverse("payment-pdf", args=[self.payment_id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # les médecins ont accès selon can_access_payment

    def test_csv_export_returns_csv_with_header(self):
        response = self.client.get(reverse("payment-export-csv"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode()
        self.assertIn("amount", content.splitlines()[0])

    def test_csv_export_records_audit_log(self):
        from common.models import AuditLog

        self.client.get(reverse("payment-export-csv"))
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.EXPORT, model_name="payment").exists())


class PaymentCrossClinicFKTests(APITestCase):
    """Couverture de non-régression pour le constat d'audit sur l'injection de FK cross-tenant : un
    comptable ne doit pas pouvoir régler (et ainsi faire basculer le statut de) la facture d'une
    autre clinique."""

    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.accountant_a = create_user(clinic=self.clinic_a, role="accountant")
        patient_b = Patient.objects.create(
            clinic=self.clinic_b,
            patient_number="PAT-2026-00003",
            first_name="Test",
            last_name="Patient",
            phone="0600000002",
            date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.invoice_b = Invoice.objects.create(
            clinic=self.clinic_b,
            patient=patient_b,
            number="INV-2026-00003",
            issue_date=date.today(),
            subtotal="100.00",
            vat_rate="0.18",
            vat_amount="18.00",
            total_amount="118.00",
            status=Invoice.Status.ISSUED,
        )
        self.client.force_authenticate(self.accountant_a)

    def test_cannot_pay_another_clinics_invoice(self):
        payload = {
            "invoice": self.invoice_b.id,
            "amount": "118.00",
            "method": "cash",
            "date": date.today().isoformat(),
        }
        response = self.client.post(reverse("payment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.invoice_b.refresh_from_db()
        self.assertEqual(self.invoice_b.status, Invoice.Status.ISSUED)


class PaymentSearchTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.accountant = create_user(clinic=self.clinic, role="accountant")
        self.patient_match = Patient.objects.create(
            clinic=self.clinic, patient_number="PAT-2026-00030", first_name="Aminata",
            last_name="Diallo", phone="0611111111", date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.patient_other = Patient.objects.create(
            clinic=self.clinic, patient_number="PAT-2026-00031", first_name="Boubacar",
            last_name="Kane", phone="0622222222", date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.invoice_match = Invoice.objects.create(
            clinic=self.clinic, patient=self.patient_match, number="INV-2026-00030",
            issue_date=date.today(), subtotal="100.00", vat_rate="0.18", vat_amount="18.00",
            total_amount="118.00", status=Invoice.Status.ISSUED,
        )
        self.invoice_other = Invoice.objects.create(
            clinic=self.clinic, patient=self.patient_other, number="INV-2026-00031",
            issue_date=date.today(), subtotal="100.00", vat_rate="0.18", vat_amount="18.00",
            total_amount="118.00", status=Invoice.Status.ISSUED,
        )
        self.payment_match = Payment.objects.create(
            clinic=self.clinic, invoice=self.invoice_match, amount="118.00", method=Payment.Method.CASH,
            status=Payment.Status.VALIDATED, date=date.today(),
        )
        self.payment_other = Payment.objects.create(
            clinic=self.clinic, invoice=self.invoice_other, amount="118.00", method=Payment.Method.CASH,
            status=Payment.Status.VALIDATED, date=date.today(),
        )
        self.client.force_authenticate(self.accountant)

    def test_search_by_patient_last_name_filters_results(self):
        response = self.client.get(reverse("payment-list"), {"search": "Diallo"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.payment_match.id, ids)
        self.assertNotIn(self.payment_other.id, ids)

    def test_search_by_invoice_number_filters_results(self):
        response = self.client.get(reverse("payment-list"), {"search": "00030"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.payment_match.id, ids)
        self.assertNotIn(self.payment_other.id, ids)
