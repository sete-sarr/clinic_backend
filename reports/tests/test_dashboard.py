"""Graphiques du tableau de bord (reports/services/dashboard.py) : valeurs, sections par rôle et
isolation entre cliniques."""

from datetime import date, time, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from appointments.models import Appointment
from billing.models import Invoice
from common.testing import create_clinic, create_user
from doctors.models import Doctor
from patients.models import Patient
from payments.models import Payment
from reports.services.dashboard import _first_day_months_ago


def _patient(clinic, number):
    return Patient.objects.create(
        clinic=clinic, patient_number=number, first_name="Test", last_name="Patient", phone="0600000000",
        date_of_birth=date(1990, 1, 1), gender=Patient.Gender.OTHER,
    )


def _doctor(clinic, email):
    user = create_user(clinic=clinic, role="doctor", email=email)
    return Doctor.objects.create(user=user, clinic=clinic, professional_number=f"DOC-{user.pk}", specialty="General")


class DashboardStatsTests(APITestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.clinic = create_clinic("Clinique A")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.patient = _patient(self.clinic, "PAT-1")
        self.doctor = _doctor(self.clinic, "doc1@example.com")
        self.other_doctor = _doctor(self.clinic, "doc2@example.com")
        self.url = reverse("dashboard-stats")

    def _appointment(self, days_ago, appointment_status, doctor=None, clinic=None, patient=None, hour=9):
        return Appointment.objects.create(
            clinic=clinic or self.clinic, patient=patient or self.patient, doctor=doctor or self.doctor,
            date=self.today - timedelta(days=days_ago), time=time(hour, 0), status=appointment_status,
        )

    def _invoice(self, invoice_status, total, number, currency="XOF", clinic=None, patient=None, days_ago=1):
        return Invoice.objects.create(
            clinic=clinic or self.clinic, patient=patient or self.patient, number=number,
            issue_date=self.today - timedelta(days=days_ago), subtotal=Decimal(total), total_amount=Decimal(total),
            status=invoice_status, currency=currency,
        )

    def _payment(self, invoice, amount, payment_status=Payment.Status.VALIDATED, on=None):
        return Payment.objects.create(
            clinic=invoice.clinic, invoice=invoice, amount=Decimal(amount), method=Payment.Method.CASH,
            status=payment_status, date=on or self.today,
        )

    def test_appointments_are_counted_per_day_over_30_days(self):
        self._appointment(0, Appointment.Status.COMPLETED, hour=8)
        self._appointment(0, Appointment.Status.COMPLETED, hour=9)
        self._appointment(2, Appointment.Status.NO_SHOW)
        self._appointment(3, Appointment.Status.CANCELLED, doctor=self.other_doctor)
        self._appointment(29, Appointment.Status.COMPLETED)  # premier jour de la fenêtre
        self._appointment(30, Appointment.Status.COMPLETED)  # hors fenêtre
        self._appointment(1, Appointment.Status.CONFIRMED)  # pas encore clôturé : ignoré

        self.client.force_authenticate(self.admin)
        data = self.client.get(self.url).data["appointments"]

        self.assertEqual(len(data["days"]), 30)
        self.assertEqual(data["days"][-1], {"date": self.today.isoformat(), "completed": 2, "no_show": 0, "cancelled": 0})
        self.assertEqual(data["totals"], {"completed": 3, "no_show": 1, "cancelled": 1})
        self.assertEqual(data["no_show_rate"], 0.25)  # 1 absence / (3 honorés + 1 absence)

    def test_revenue_sums_validated_payments_per_month_in_clinic_currency(self):
        invoice = self._invoice(Invoice.Status.PAID, "50000", "INV-1")
        self._payment(invoice, "30000")
        self._payment(invoice, "20000")
        self._payment(invoice, "9999", payment_status=Payment.Status.REFUNDED)
        old_month = _first_day_months_ago(self.today, 2)
        self._payment(invoice, "12000", on=old_month)
        self._payment(invoice, "1000", on=_first_day_months_ago(self.today, 6))  # hors fenêtre
        euro_invoice = self._invoice(Invoice.Status.PAID, "100", "INV-2", currency="EUR")
        self._payment(euro_invoice, "100")  # autre devise : non additionné

        self.client.force_authenticate(self.admin)
        data = self.client.get(self.url).data

        self.assertEqual(data["currency"], "XOF")
        months = data["revenue"]["months"]
        self.assertEqual(len(months), 6)
        self.assertEqual(months[-1], {"month": self.today.strftime("%Y-%m"), "amount": "50000.00"})
        self.assertEqual(months[-3], {"month": old_month.strftime("%Y-%m"), "amount": "12000.00"})
        self.assertEqual(data["revenue"]["total"], "62000.00")

    def test_invoices_by_status_and_balance_due(self):
        self._invoice(Invoice.Status.PAID, "10000", "INV-1")
        partial = self._invoice(Invoice.Status.PENDING_PAYMENT, "20000", "INV-2")
        self._payment(partial, "5000")
        self._invoice(Invoice.Status.ISSUED, "7000", "INV-3")
        self._invoice(Invoice.Status.DRAFT, "99999", "INV-4")  # brouillon : ignoré
        self._invoice(Invoice.Status.CANCELLED, "88888", "INV-5")  # annulée : ignorée

        self.client.force_authenticate(self.admin)
        data = self.client.get(self.url).data["invoices"]

        self.assertEqual(data["statuses"], [
            {"status": "paid", "count": 1, "amount": "10000.00"},
            {"status": "pending_payment", "count": 1, "amount": "20000.00"},
            {"status": "issued", "count": 1, "amount": "7000.00"},
        ])
        self.assertEqual(data["balance_due"], "22000.00")  # 15 000 + 7 000

    def test_doctor_sees_only_own_appointments_and_no_financial_data(self):
        self._appointment(1, Appointment.Status.COMPLETED)
        self._appointment(1, Appointment.Status.COMPLETED, doctor=self.other_doctor, hour=10)
        self.client.force_authenticate(self.doctor.user)
        data = self.client.get(self.url).data

        self.assertEqual(data["appointments"]["totals"]["completed"], 1)
        self.assertIsNone(data["revenue"])
        self.assertIsNone(data["invoices"])

    def test_sections_by_role(self):
        expectations = {
            "secretary": (True, False),
            "accountant": (False, True),
            "clinic_admin": (True, True),
        }
        for role, (sees_appointments, sees_finance) in expectations.items():
            with self.subTest(role=role):
                self.client.force_authenticate(create_user(clinic=self.clinic, role=role))
                data = self.client.get(self.url).data
                self.assertEqual(data["appointments"] is not None, sees_appointments)
                self.assertEqual(data["revenue"] is not None, sees_finance)
                self.assertEqual(data["invoices"] is not None, sees_finance)

    def test_patient_and_pharmacist_are_refused(self):
        for role in ("patient", "pharmacist"):
            with self.subTest(role=role):
                self.client.force_authenticate(create_user(clinic=self.clinic, role=role))
                self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_other_clinics_data_is_never_counted(self):
        other = create_clinic("Clinique B")
        other_patient = _patient(other, "PAT-B")
        other_doctor = _doctor(other, "docb@example.com")
        self._appointment(1, Appointment.Status.COMPLETED, doctor=other_doctor, clinic=other, patient=other_patient)
        invoice = self._invoice(Invoice.Status.ISSUED, "5000", "INV-B", clinic=other, patient=other_patient)
        self._payment(invoice, "1000")

        self.client.force_authenticate(self.admin)
        data = self.client.get(self.url).data
        self.assertEqual(sum(data["appointments"]["totals"].values()), 0)
        self.assertEqual(data["revenue"]["total"], "0.00")
        self.assertEqual([s["count"] for s in data["invoices"]["statuses"]], [0, 0, 0])
        self.assertEqual(data["invoices"]["balance_due"], "0.00")
