"""Codes-barres des médicaments (pharmacy/barcodes.py) : normalisation GTIN, lecture DataMatrix GS1,
unicité par clinique et recherche par scan."""

from datetime import date

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from pharmacy.barcodes import GS, normalize_gtin, parse_scanned_code
from pharmacy.models import Medication

EAN13 = "4006381333931"


class BarcodeParsingTests(SimpleTestCase):
    def test_every_reading_of_the_same_box_gives_the_same_code(self):
        self.assertEqual(normalize_gtin(EAN13), EAN13)
        self.assertEqual(normalize_gtin("0" + EAN13), EAN13)  # GTIN-14, indicateur 0
        self.assertEqual(normalize_gtin(" 4006381 333931 "), EAN13)
        self.assertEqual(normalize_gtin("036000291452"), "0036000291452")  # UPC-A
        self.assertEqual(normalize_gtin("96385074"), "96385074")  # EAN-8

    def test_invalid_codes_are_rejected(self):
        for code in ("4006381333932", "12345", "40063813339AB", ""):
            with self.assertRaises(ValidationError):
                normalize_gtin(code)

    def test_datamatrix_gives_gtin_lot_and_expiry(self):
        scanned = parse_scanned_code(f"]d2{GS}010{EAN13}17271130" + "10LOT42A" + f"{GS}21SER9")
        self.assertEqual((scanned.gtin, scanned.batch_number, scanned.expiry_date), (EAN13, "LOT42A", date(2027, 11, 30)))

    def test_readable_form_and_day_00_mean_end_of_month(self):
        scanned = parse_scanned_code(f"(01)0{EAN13}(17)270200(10)AB12")
        self.assertEqual((scanned.batch_number, scanned.expiry_date), ("AB12", date(2027, 2, 28)))

    def test_linear_code_has_no_lot(self):
        scanned = parse_scanned_code(EAN13)
        self.assertEqual((scanned.gtin, scanned.batch_number, scanned.expiry_date), (EAN13, "", None))

    def test_code_without_gtin_is_rejected(self):
        with self.assertRaises(ValidationError):
            parse_scanned_code("(10)AB12")


class MedicationBarcodeApiTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.client.force_authenticate(self.pharmacist)
        self.doliprane = Medication.objects.create(clinic=self.clinic, name="Doliprane", unit="boîte", barcode=EAN13)

    def _create(self, name, barcode):
        return self.client.post(reverse("medication-list"), {"name": name, "unit": "boîte", "barcode": barcode}, format="json")

    def test_barcode_is_normalized_and_unique_in_the_clinic(self):
        response = self._create("Efferalgan", "0" + EAN13)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "barcode")
        self.assertIn("Doliprane", response.data["message"])
        response = self._create("Spasfon", "036000291452")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["barcode"], "0036000291452")
        self.assertEqual(self._create("Smecta", "").status_code, status.HTTP_201_CREATED)
        self.assertEqual(self._create("Gaviscon", "").status_code, status.HTTP_201_CREATED)

    def test_same_barcode_is_allowed_in_another_clinic(self):
        other = create_clinic()
        Medication.objects.create(clinic=other, name="Doliprane", unit="boîte", barcode=EAN13)
        self.assertEqual(Medication.objects.filter(barcode=EAN13).count(), 2)

    def test_scan_finds_the_medication_with_lot_and_expiry(self):
        response = self.client.get(reverse("medication-scan"), {"code": f"{GS}010{EAN13}17280315" + "10B7"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["medication"]["id"], self.doliprane.id)
        self.assertEqual((response.data["batch_number"], response.data["expiry_date"]), ("B7", "2028-03-15"))

    def test_scan_of_an_unknown_or_foreign_code(self):
        other = create_clinic()
        Medication.objects.create(clinic=other, name="Spasfon", unit="boîte", barcode="96385074")
        response = self.client.get(reverse("medication-scan"), {"code": "96385074"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["medication"])
        response = self.client.get(reverse("medication-scan"), {"code": "123"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patient_cannot_scan(self):
        self.client.force_authenticate(create_user(clinic=self.clinic, role="patient"))
        self.assertEqual(self.client.get(reverse("medication-scan"), {"code": EAN13}).status_code, 403)
