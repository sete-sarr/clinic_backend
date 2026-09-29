"""Devise de facturation choisie par la clinique (docs/i18n.md §8) : formatage identique à l'écran
(frontend/src/app/core/utils/money.spec.ts reprend les mêmes cas) et devise figée sur chaque facture."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import Invoice
from billing.services import create_invoice
from billing.services.pdf import render_invoice_pdf
from common.currency import format_money
from common.testing import create_clinic, create_user
from patients.models import Patient

NNBSP, NBSP = " ", " "


class FormatMoneyTests(SimpleTestCase):
    def test_french_and_english_formats(self):
        cases = [
            (Decimal("10000.00"), "XOF", "fr", f"10{NNBSP}000{NBSP}F CFA"),
            (Decimal("10000.00"), "XOF", "en", f"10,000{NBSP}F CFA"),
            (Decimal("180.90"), "XOF", "fr", f"180,90{NBSP}F CFA"),  # centimes jamais masqués
            (Decimal("1250.5"), "EUR", "fr", f"1{NNBSP}250,50{NBSP}€"),
            (Decimal("1250.5"), "EUR", "en", "€1,250.50"),
            (Decimal("99"), "USD", "en", "$99.00"),
            (Decimal("1234567.891"), "CHF", "en", f"CHF{NBSP}1,234,567.89"),
            (Decimal("0"), "GNF", "fr", f"0{NBSP}FG"),
        ]
        for amount, currency, language, expected in cases:
            with self.subTest(currency=currency, language=language, amount=amount):
                self.assertEqual(format_money(amount, currency, language), expected)

    def test_empty_amount(self):
        self.assertEqual(format_money(None, "EUR", "fr"), "")


def _patient(clinic):
    return Patient.objects.create(
        clinic=clinic, patient_number="PAT-2026-00300", first_name="Ada", last_name="Lovelace", phone="770000000",
        date_of_birth=date(1990, 1, 1), gender=Patient.Gender.FEMALE,
    )


def _invoice(clinic, patient, price="1250.50"):
    return create_invoice(
        clinic=clinic, patient=patient, vat_rate=Decimal("0"), issue_date=date(2026, 10, 1),
        lines=[{"description": "Consultation", "quantity": 1, "unit_price": Decimal(price)}],
    )


class ClinicCurrencyTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Devise")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.url = reverse("clinic-detail", args=[self.clinic.id])

    def test_default_currency_is_fcfa(self):
        self.assertEqual(self.clinic.currency, "XOF")

    def test_admin_chooses_the_currency_and_unknown_codes_are_rejected(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.patch(self.url, {"currency": "EUR"}, format="json").status_code, status.HTTP_200_OK)
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.currency, "EUR")
        response = self.client.patch(self.url, {"currency": "BTC"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "currency")

    def test_other_roles_cannot_change_the_currency(self):
        self.client.force_authenticate(create_user(clinic=self.clinic, role="accountant"))
        response = self.client.patch(self.url, {"currency": "EUR"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_existing_invoices_keep_their_currency(self):
        patient = _patient(self.clinic)
        old = _invoice(self.clinic, patient)
        self.clinic.currency = "EUR"
        self.clinic.save(update_fields=["currency"])
        new = _invoice(self.clinic, patient)
        old.refresh_from_db()
        self.assertEqual((old.currency, new.currency), ("XOF", "EUR"))

    def test_invoice_payment_and_user_payloads_expose_the_currency(self):
        self.clinic.currency = "USD"
        self.clinic.save(update_fields=["currency"])
        accountant = create_user(clinic=self.clinic, role="accountant")
        self.client.force_authenticate(accountant)
        invoice = _invoice(self.clinic, _patient(self.clinic))
        Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.ISSUED)

        self.assertEqual(self.client.get(reverse("invoice-detail", args=[invoice.id])).data["currency"], "USD")
        payment = self.client.post(
            reverse("payment-list"), {"invoice": invoice.id, "amount": "100.00", "method": "cash", "date": "2026-10-02"}, format="json"
        )
        self.assertEqual(payment.status_code, status.HTTP_201_CREATED, payment.data)
        self.assertEqual(payment.data["currency"], "USD")
        self.assertEqual(self.client.get(reverse("me")).data["clinic_currency"], "USD")

    def test_overpayment_message_shows_formatted_amounts(self):
        self.client.force_authenticate(create_user(clinic=self.clinic, role="accountant"))
        invoice = _invoice(self.clinic, _patient(self.clinic), price="10000")
        Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.ISSUED)
        response = self.client.post(
            reverse("payment-list"), {"invoice": invoice.id, "amount": "15000", "method": "cash", "date": "2026-10-02"}, format="json",
            HTTP_ACCEPT_LANGUAGE="fr",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(f"15{NNBSP}000{NBSP}F CFA", response.data["message"])
        self.assertIn(f"10{NNBSP}000{NBSP}F CFA", response.data["message"])

    def test_invoice_pdf_shows_amounts_in_the_invoice_currency(self):
        self.clinic.currency = "EUR"
        self.clinic.locale = "en"
        self.clinic.save(update_fields=["currency", "locale"])
        invoice = _invoice(self.clinic, _patient(self.clinic))
        with mock.patch("common.pdf.HTML") as html:
            html.return_value.write_pdf.return_value = b"%PDF-test"
            render_invoice_pdf(invoice=invoice, user=self.admin)
        self.assertIn("€1,250.50", html.call_args.kwargs["string"])
