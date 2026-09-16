from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from billing.services import cancel_invoice, create_invoice, issue_invoice, update_invoice
from common.testing import create_clinic, create_user
from communication.models import NotificationLog
from patients.models import Patient
from pharmacy.models import Medication, StockMovement
from pharmacy.services import (
    adjust_stock,
    create_medication,
    receive_stock_batch,
    sync_invoice_stock,
    update_medication,
)


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


class ReceiveStockBatchTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.medication = create_medication(
            clinic=self.clinic, actor=self.pharmacist, name="Paracétamol 500mg", unit="boîte"
        )

    def test_receiving_a_batch_increases_current_stock(self):
        receive_stock_batch(
            medication=self.medication,
            actor=self.pharmacist,
            batch_number="LOT-001",
            expiry_date=date.today() + timedelta(days=365),
            received_date=date.today(),
            quantity_received=50,
            unit_cost=Decimal("2.50"),
        )
        self.medication.refresh_from_db()
        self.assertEqual(self.medication.current_stock, 50)
        self.assertEqual(
            StockMovement.objects.filter(medication=self.medication, movement_type="purchase").count(), 1
        )


class ThresholdAlertTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        create_user(clinic=self.clinic, role="clinic_admin", email="admin@example.com")
        self.medication = create_medication(
            clinic=self.clinic,
            actor=self.pharmacist,
            name="Amoxicilline 500mg",
            unit="boîte",
            min_threshold=10,
            max_threshold=100,
        )

    def test_low_stock_alert_fires_once_then_resets(self):
        with patch("pharmacy.notifications.send_low_stock_alert") as mock_alert:
            with self.captureOnCommitCallbacks(execute=True):
                receive_stock_batch(
                    medication=self.medication,
                    actor=self.pharmacist,
                    batch_number="LOT-A",
                    expiry_date=date.today() + timedelta(days=180),
                    received_date=date.today(),
                    quantity_received=5,
                )
        mock_alert.assert_called_once_with(medication_id=self.medication.id)
        self.medication.refresh_from_db()
        self.assertTrue(self.medication.low_stock_alerted)

        # Un deuxième mouvement qui reste sous le seuil ne doit pas re-déclencher l'alerte
        # (débounce, pharmacy/services.py::check_stock_thresholds).
        with patch("pharmacy.notifications.send_low_stock_alert") as mock_alert:
            with self.captureOnCommitCallbacks(execute=True):
                receive_stock_batch(
                    medication=self.medication,
                    actor=self.pharmacist,
                    batch_number="LOT-B",
                    expiry_date=date.today() + timedelta(days=180),
                    received_date=date.today(),
                    quantity_received=1,
                )
        mock_alert.assert_not_called()

    def test_overstock_alert_fires_when_max_threshold_exceeded(self):
        with patch("pharmacy.notifications.send_overstock_alert") as mock_alert:
            with self.captureOnCommitCallbacks(execute=True):
                receive_stock_batch(
                    medication=self.medication,
                    actor=self.pharmacist,
                    batch_number="LOT-C",
                    expiry_date=date.today() + timedelta(days=180),
                    received_date=date.today(),
                    quantity_received=150,
                )
        mock_alert.assert_called_once_with(medication_id=self.medication.id)
        self.medication.refresh_from_db()
        self.assertTrue(self.medication.overstock_alerted)

    def test_changing_threshold_alone_can_trigger_alert(self):
        receive_stock_batch(
            medication=self.medication,
            actor=self.pharmacist,
            batch_number="LOT-D",
            expiry_date=date.today() + timedelta(days=180),
            received_date=date.today(),
            quantity_received=20,
        )
        with patch("pharmacy.notifications.send_low_stock_alert") as mock_alert:
            with self.captureOnCommitCallbacks(execute=True):
                update_medication(medication=self.medication, actor=self.pharmacist, min_threshold=25)
        mock_alert.assert_called_once_with(medication_id=self.medication.id)


class AdjustStockTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.medication = create_medication(clinic=self.clinic, actor=self.pharmacist, name="Ibuprofène", unit="boîte")
        self.batch = receive_stock_batch(
            medication=self.medication,
            actor=self.pharmacist,
            batch_number="LOT-001",
            expiry_date=date.today() + timedelta(days=365),
            received_date=date.today(),
            quantity_received=30,
        )

    def test_negative_adjustment_decreases_stock(self):
        adjust_stock(batch=self.batch, actor=self.pharmacist, quantity_delta=-5, reason="Casse")
        self.medication.refresh_from_db()
        self.assertEqual(self.medication.current_stock, 25)

    def test_adjustment_cannot_make_batch_negative(self):
        with self.assertRaises(ValidationError):
            adjust_stock(batch=self.batch, actor=self.pharmacist, quantity_delta=-100, reason="Erreur")


class InvoiceStockIntegrationTests(TestCase):
    """Vérifie l'intégration billing <-> pharmacy : sync_invoice_stock appelée depuis
    issue_invoice/update_invoice/cancel_invoice (billing/services/__init__.py)."""

    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.accountant = create_user(clinic=self.clinic, role="accountant")
        self.patient = _create_patient(self.clinic)
        self.medication = create_medication(clinic=self.clinic, actor=self.pharmacist, name="Doliprane", unit="boîte")
        # Deux lots, le premier périme plus tôt : le FEFO doit le consommer en premier.
        self.early_batch = receive_stock_batch(
            medication=self.medication,
            actor=self.pharmacist,
            batch_number="EARLY",
            expiry_date=date.today() + timedelta(days=30),
            received_date=date.today(),
            quantity_received=5,
        )
        self.late_batch = receive_stock_batch(
            medication=self.medication,
            actor=self.pharmacist,
            batch_number="LATE",
            expiry_date=date.today() + timedelta(days=365),
            received_date=date.today(),
            quantity_received=20,
        )

    def _create_draft_invoice(self, quantity):
        return create_invoice(
            clinic=self.clinic,
            patient=self.patient,
            issue_date=date.today(),
            lines=[{"description": "Doliprane", "quantity": quantity, "unit_price": Decimal("5.00"), "medication": self.medication}],
        )

    def test_draft_invoice_does_not_touch_stock(self):
        self._create_draft_invoice(3)
        self.medication.refresh_from_db()
        self.assertEqual(self.medication.current_stock, 25)

    def test_issuing_invoice_dispenses_fefo(self):
        invoice = self._create_draft_invoice(8)
        issue_invoice(invoice=invoice, actor=self.accountant)

        self.early_batch.refresh_from_db()
        self.late_batch.refresh_from_db()
        self.medication.refresh_from_db()
        self.assertEqual(self.early_batch.quantity_remaining, 0)  # 5 pris en premier (FEFO)
        self.assertEqual(self.late_batch.quantity_remaining, 17)  # 3 restants pris sur l'autre lot
        self.assertEqual(self.medication.current_stock, 17)

    def test_cancelling_issued_invoice_restores_stock(self):
        invoice = self._create_draft_invoice(8)
        issue_invoice(invoice=invoice, actor=self.accountant)
        cancel_invoice(invoice=invoice, actor=self.accountant)

        self.early_batch.refresh_from_db()
        self.late_batch.refresh_from_db()
        self.medication.refresh_from_db()
        self.assertEqual(self.early_batch.quantity_remaining, 5)
        self.assertEqual(self.late_batch.quantity_remaining, 20)
        self.assertEqual(self.medication.current_stock, 25)

    def test_reducing_quantity_after_issuance_returns_the_difference(self):
        invoice = self._create_draft_invoice(8)
        issue_invoice(invoice=invoice, actor=self.accountant)

        update_invoice(
            invoice=invoice,
            actor=self.accountant,
            lines=[{"description": "Doliprane", "quantity": 2, "unit_price": Decimal("5.00"), "medication": self.medication}],
        )

        self.medication.refresh_from_db()
        # 2 dispensés au total désormais au lieu de 8 -> 6 rendus au stock.
        self.assertEqual(self.medication.current_stock, 23)

    def test_issuing_invoice_with_insufficient_stock_raises(self):
        invoice = self._create_draft_invoice(999)
        with self.assertRaises(ValidationError):
            issue_invoice(invoice=invoice, actor=self.accountant)

    def test_medication_from_another_clinic_is_rejected(self):
        other_clinic = create_clinic("Other Clinic")
        other_pharmacist = create_user(clinic=other_clinic, role="pharmacist")
        other_medication = create_medication(clinic=other_clinic, actor=other_pharmacist, name="Autre", unit="boîte")
        with self.assertRaises(ValidationError):
            create_invoice(
                clinic=self.clinic,
                patient=self.patient,
                issue_date=date.today(),
                lines=[
                    {
                        "description": "Autre",
                        "quantity": 1,
                        "unit_price": Decimal("5.00"),
                        "medication": other_medication,
                    }
                ],
            )
