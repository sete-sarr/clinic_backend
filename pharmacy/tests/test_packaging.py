"""Vente au conditionnement et au détail (décision produit du 2026-10-08) : le stock est compté en
unités de base ; la boîte n'est qu'un facteur de conversion, à la réception comme à la vente."""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.services import cancel_invoice, create_invoice, issue_invoice
from common.models import AuditLog
from common.testing import create_clinic, create_user
from patients.models import Patient
from pharmacy.models import SaleUnit, StockMovement
from pharmacy.services import create_medication, receive_stock_batch, split_medication_packs, update_medication


def _patient(clinic):
    return Patient.objects.create(
        clinic=clinic, patient_number="PAT-1", first_name="Test", last_name="Patient", phone="0600000000",
        date_of_birth=date(1990, 1, 1), gender=Patient.Gender.OTHER,
    )


def _receive(medication, actor, quantity, received_in, days=365, number="L1"):
    return receive_stock_batch(
        medication=medication, actor=actor, batch_number=number, expiry_date=date.today() + timedelta(days=days),
        received_date=date.today(), quantity=quantity, received_in=received_in, unit_cost=Decimal("4000.00"),
    )


class PackagedSaleTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.patient = _patient(self.clinic)
        self.medication = create_medication(
            clinic=self.clinic, actor=self.pharmacist, name="Paracétamol 500 mg", unit="comprimé",
            unit_price=Decimal("100.00"), pack_unit="boîte", units_per_pack=50, pack_price=Decimal("4500.00"),
        )

    def _invoice(self, *lines):
        invoice = create_invoice(
            clinic=self.clinic, patient=self.patient, issue_date=date.today(),
            lines=[
                {"description": "Paracétamol", "quantity": quantity, "unit_price": Decimal("1"),
                 "medication": self.medication, "sale_unit": sale_unit}
                for quantity, sale_unit in lines
            ],
        )
        issue_invoice(invoice=invoice, actor=self.pharmacist)
        return invoice

    def _stock(self):
        self.medication.refresh_from_db()
        return self.medication.current_stock

    def test_reception_in_packs_or_units_is_counted_in_units(self):
        batch = _receive(self.medication, self.pharmacist, 10, SaleUnit.PACK)
        self.assertEqual((batch.quantity_received, batch.units_per_pack, batch.received_in), (500, 50, "pack"))
        _receive(self.medication, self.pharmacist, 37, SaleUnit.UNIT, number="L2")
        self.assertEqual(self._stock(), 537)

    def test_selling_packs_and_single_units(self):
        _receive(self.medication, self.pharmacist, 10, SaleUnit.PACK)
        invoice = self._invoice((1, SaleUnit.PACK), (3, SaleUnit.UNIT))
        self.assertEqual(sorted(invoice.lines.values_list("stock_quantity", flat=True)), [3, 50])
        self.assertEqual(self._stock(), 447)

        cancel_invoice(invoice=invoice, actor=self.pharmacist)
        self.assertEqual(self._stock(), 500)

    def test_unit_sale_follows_expiry_order_across_batches(self):
        early = _receive(self.medication, self.pharmacist, 1, SaleUnit.PACK, days=30, number="EARLY")
        _receive(self.medication, self.pharmacist, 1, SaleUnit.PACK, days=400, number="LATE")
        self._invoice((52, SaleUnit.UNIT))
        early.refresh_from_db()
        self.assertEqual(early.quantity_remaining, 0)
        self.assertEqual(self._stock(), 48)

    def test_unit_sale_refused_when_not_allowed(self):
        update_medication(medication=self.medication, actor=self.pharmacist, allow_unit_sale=False)
        _receive(self.medication, self.pharmacist, 1, SaleUnit.PACK)
        with self.assertRaises(ValidationError):
            self._invoice((3, SaleUnit.UNIT))
        self._invoice((1, SaleUnit.PACK))
        self.assertEqual(self._stock(), 0)

    def test_cancel_after_pack_size_change_returns_what_was_taken(self):
        _receive(self.medication, self.pharmacist, 2, SaleUnit.PACK)
        invoice = self._invoice((1, SaleUnit.PACK))
        update_medication(medication=self.medication, actor=self.pharmacist, units_per_pack=30)
        cancel_invoice(invoice=invoice, actor=self.pharmacist)
        self.assertEqual(self._stock(), 100)

    def test_insufficient_stock_message_uses_the_base_unit(self):
        _receive(self.medication, self.pharmacist, 1, SaleUnit.PACK)
        with self.assertRaisesMessage(ValidationError, "il manque 10 comprimé"):
            self._invoice((60, SaleUnit.UNIT))


class SplitPacksTests(TestCase):
    """« Détailler le stock » : un médicament compté en boîtes passe en comprimés."""

    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.patient = _patient(self.clinic)
        self.medication = create_medication(
            clinic=self.clinic, actor=self.pharmacist, name="Amoxicilline", unit="boîte",
            unit_price=Decimal("3000.00"), min_threshold=2, max_threshold=20,
        )
        _receive(self.medication, self.pharmacist, 10, SaleUnit.UNIT)
        self.invoice = create_invoice(
            clinic=self.clinic, patient=self.patient, issue_date=date.today(),
            lines=[{"description": "Amoxicilline (boîte)", "quantity": 2, "unit_price": Decimal("3000.00"),
                    "medication": self.medication}],
        )
        issue_invoice(invoice=self.invoice, actor=self.pharmacist)

    def test_packaging_cannot_be_changed_directly_once_stock_exists(self):
        with self.assertRaises(ValidationError):
            update_medication(medication=self.medication, actor=self.pharmacist, units_per_pack=12, pack_unit="boîte")

    def test_split_converts_stock_history_thresholds_and_past_invoices(self):
        medication = split_medication_packs(
            medication=self.medication, actor=self.pharmacist, units_per_pack=12, unit="gélule",
            unit_price=Decimal("300.00"),
        )
        self.assertEqual((medication.unit, medication.pack_unit, medication.units_per_pack), ("gélule", "boîte", 12))
        self.assertEqual((medication.pack_price, medication.unit_price), (Decimal("3000.00"), Decimal("300.00")))
        self.assertEqual((medication.current_stock, medication.min_threshold, medication.max_threshold), (96, 24, 240))
        batch = medication.batches.get()
        self.assertEqual((batch.quantity_received, batch.quantity_remaining), (120, 96))
        self.assertEqual(sum(StockMovement.objects.filter(medication=medication).values_list("quantity_delta", flat=True)), 96)
        line = self.invoice.lines.get()
        self.assertEqual((line.sale_unit, line.stock_quantity, line.quantity), ("pack", 24, 2))
        self.assertTrue(AuditLog.objects.filter(model_name="Medication", metadata__has_key="split_packs").exists())

        # Une facture antérieure annulée après conversion rend ses 2 boîtes, soit 24 gélules.
        cancel_invoice(invoice=self.invoice, actor=self.pharmacist)
        medication.refresh_from_db()
        self.assertEqual(medication.current_stock, 120)

    def test_split_only_once(self):
        split_medication_packs(
            medication=self.medication, actor=self.pharmacist, units_per_pack=12, unit="gélule", unit_price=Decimal("1")
        )
        self.medication.refresh_from_db()
        with self.assertRaises(ValidationError):
            split_medication_packs(
                medication=self.medication, actor=self.pharmacist, units_per_pack=2, unit="x", unit_price=Decimal("1")
            )


class PackagingApiTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.client.force_authenticate(self.pharmacist)

    def _create(self, **extra):
        return self.client.post(
            reverse("medication-list"),
            {"name": "Ibuprofène", "unit": "comprimé", "unit_price": "50.00", **extra},
            format="json",
        )

    def test_pack_unit_required_for_packaged_medication(self):
        response = self._create(units_per_pack=20, pack_price="900.00")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "pack_unit")

    def test_unpackaged_medication_pack_price_follows_unit_price(self):
        response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual((response.data["units_per_pack"], response.data["pack_price"]), (1, "50.00"))

    def test_receive_in_packs_through_api(self):
        medication_id = self._create(units_per_pack=20, pack_unit="boîte", pack_price="900.00").data["id"]
        response = self.client.post(
            reverse("stock-batch-list"),
            {"medication": medication_id, "batch_number": "B1", "expiry_date": "2030-01-01",
             "received_date": "2026-10-08", "quantity": 3, "received_in": "pack"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual((response.data["quantity_received"], response.data["units_per_pack"]), (60, 20))

    def test_split_packs_endpoint_is_reserved_to_stock_managers(self):
        medication_id = self.client.post(
            reverse("medication-list"), {"name": "Sirop", "unit": "boîte", "unit_price": "10.00"}, format="json"
        ).data["id"]
        url = reverse("medication-split-packs", args=[medication_id])
        payload = {"units_per_pack": 10, "unit": "sachet", "unit_price": "1.50"}

        self.client.force_authenticate(create_user(clinic=self.clinic, role="secretary"))
        self.assertEqual(self.client.post(url, payload, format="json").status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.pharmacist)
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual((response.data["unit"], response.data["pack_unit"], response.data["units_per_pack"]), ("sachet", "boîte", 10))
