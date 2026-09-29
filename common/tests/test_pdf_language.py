"""Langue des documents PDF (docs/i18n.md §2) : celle de la CLINIQUE, jamais celle de l'utilisateur
qui imprime ni de la requête — une facture ou un ticket remis au patient est identique quel que soit
le poste qui le produit. Le HTML passé à WeasyPrint est capturé pour vérifier les libellés."""

from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone, translation

from appointments.models import Appointment
from appointments.pdf import render_checkin_ticket_pdf
from billing.models import Invoice, InvoiceLine
from billing.services.pdf import render_invoice_pdf
from common.testing import create_clinic, create_user
from doctors.models import Doctor
from patients.models import Patient


def _clinic(name, locale):
    clinic = create_clinic(name)
    clinic.locale = locale
    clinic.save(update_fields=["locale"])
    return clinic


def _patient(clinic):
    return Patient.objects.create(
        clinic=clinic, patient_number="PAT-2026-00200", first_name="Grace", last_name="Hopper", phone="770000000",
        date_of_birth=date(1990, 1, 1), gender=Patient.Gender.FEMALE,
    )


def _doctor(clinic):
    user = create_user(clinic=clinic, role="doctor", first_name="Awa", last_name="Diop")
    return Doctor.objects.create(user=user, clinic=clinic, professional_number=f"DOC-{user.pk}", specialty="General")


def _invoice(clinic):
    patient = _patient(clinic)
    invoice = Invoice.objects.create(
        clinic=clinic, patient=patient, doctor=_doctor(clinic), number="FAC-2026-00001", issue_date=date(2026, 10, 1),
        subtotal=Decimal("10000.00"), vat_amount=Decimal("1800.00"), total_amount=Decimal("11800.00"),
        status=Invoice.Status.PENDING_PAYMENT,
    )
    InvoiceLine.objects.create(invoice=invoice, description="Consultation", quantity=1,
                               unit_price=Decimal("10000.00"), line_total=Decimal("10000.00"))
    return invoice


def _rendered_html(render, **kwargs):
    """Rend le document et renvoie le HTML transmis à WeasyPrint."""
    with mock.patch("common.pdf.HTML") as html:
        html.return_value.write_pdf.return_value = b"%PDF-test"
        render(**kwargs)
    return html.call_args.kwargs["string"]


class InvoicePdfLanguageTests(TestCase):
    def test_english_clinic_invoice_is_in_english_even_from_a_french_request(self):
        clinic = _clinic("Sunrise Clinic", "en")
        admin = create_user(clinic=clinic, role="clinic_admin", language="fr")
        with translation.override("fr"):
            html = _rendered_html(render_invoice_pdf, invoice=_invoice(clinic), user=admin)

        self.assertIn('<html lang="en">', html)
        for label in ("INVOICE", "Issue date:", "Unit price", "Subtotal", "VAT", "Total due", "Partially paid",
                      "Dr. Awa Diop", "Billing Department", "Page"):
            self.assertIn(label, html)
        self.assertIn("Oct. 1, 2026", html)  # date localisée (DATE_FORMAT anglais)
        self.assertNotIn("Total à payer", html)

    def test_french_clinic_invoice_is_in_french_even_from_an_english_request(self):
        clinic = _clinic("Clinique du Lac", "fr")
        admin = create_user(clinic=clinic, role="clinic_admin", language="en")
        with translation.override("en"):
            html = _rendered_html(render_invoice_pdf, invoice=_invoice(clinic), user=admin)

        self.assertIn('<html lang="fr">', html)
        for label in ("FACTURE", "Date d'émission :", "Prix unitaire", "Sous-total", "TVA", "Total à payer",
                      "Paiement partiel", "Dr Awa Diop", "Service Facturation"):
            self.assertIn(label, html)
        self.assertNotIn("Total due", html)

    def test_request_language_is_restored_after_rendering(self):
        clinic = _clinic("Sunrise Clinic", "en")
        admin = create_user(clinic=clinic, role="clinic_admin")
        with translation.override("fr"):
            _rendered_html(render_invoice_pdf, invoice=_invoice(clinic), user=admin)
            self.assertEqual(translation.get_language(), "fr")


class CheckinTicketPdfLanguageTests(TestCase):
    def test_standalone_ticket_follows_the_clinic_language(self):
        clinic = _clinic("Sunrise Clinic", "en")
        appointment = Appointment.objects.create(
            clinic=clinic, doctor=_doctor(clinic), patient=_patient(clinic), date=date(2026, 10, 1),
            time=datetime(2026, 10, 1, 9, 30).time(), status=Appointment.Status.CONFIRMED, checked_in_at=timezone.now(),
        )
        receptionist = create_user(clinic=clinic, role="secretary", language="fr")
        with translation.override("fr"):
            html = _rendered_html(render_checkin_ticket_pdf, appointment=appointment, user=receptionist)

        self.assertIn('<html lang="en">', html)
        for label in ("Check-in slip", "Ticket no.", "Patient ID", "Appointment date", "automatically generated document"):
            self.assertIn(label, html)
        self.assertNotIn("Ticket de passage", html)
