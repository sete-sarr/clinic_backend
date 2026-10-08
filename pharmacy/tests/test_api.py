from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from pharmacy.models import Medication


class MedicationApiTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.secretary = create_user(clinic=self.clinic, role="secretary")

    def test_pharmacist_can_create_medication(self):
        self.client.force_authenticate(self.pharmacist)
        payload = {"name": "Paracétamol 500mg", "unit": "boîte", "unit_price": "3.50", "min_threshold": 10}
        response = self.client.post(reverse("medication-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["current_stock"], 0)

    def test_secretary_cannot_create_medication(self):
        self.client.force_authenticate(self.secretary)
        payload = {"name": "Paracétamol 500mg", "unit": "boîte"}
        response = self.client.post(reverse("medication-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_secretary_can_list_medications(self):
        Medication.objects.create(clinic=self.clinic, name="Ibuprofène", unit="boîte")
        self.client.force_authenticate(self.secretary)
        response = self.client.get(reverse("medication-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]) if "results" in response.data else len(response.data), 1)

    def test_other_clinic_cannot_see_medication(self):
        Medication.objects.create(clinic=self.clinic, name="Ibuprofène", unit="boîte")
        other_clinic = create_clinic("Other Clinic")
        other_pharmacist = create_user(clinic=other_clinic, role="pharmacist")
        self.client.force_authenticate(other_pharmacist)
        response = self.client.get(reverse("medication-list"))
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 0)


class MedicationValidationTests(APITestCase):
    """Les contraintes de base (nom unique par clinique, seuil max >= seuil min) doivent être
    renvoyées en 400 {code, message, field}, jamais remonter en IntegrityError (500)."""

    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.client.force_authenticate(self.pharmacist)
        self.existing = Medication.objects.create(clinic=self.clinic, name="Doliprane", unit="boîte")

    def test_duplicate_name_in_same_clinic_is_rejected_on_name_field(self):
        response = self.client.post(reverse("medication-list"), {"name": "doliprane ", "unit": "boîte"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "name")
        self.assertEqual(response.data["message"], "Un médicament portant ce nom existe déjà dans cette clinique.")

    def test_same_name_in_another_clinic_is_allowed(self):
        other_clinic = create_clinic("Other Clinic")
        self.client.force_authenticate(create_user(clinic=other_clinic, role="pharmacist"))
        response = self.client.post(reverse("medication-list"), {"name": "Doliprane", "unit": "boîte"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_negative_unit_price_is_rejected(self):
        response = self.client.post(
            reverse("medication-list"), {"name": "Amoxicilline", "unit": "boîte", "unit_price": "-1"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "unit_price")

    def test_updating_a_medication_keeps_its_own_name(self):
        url = reverse("medication-detail", args=[self.existing.id])
        response = self.client.patch(url, {"name": "Doliprane", "unit_price": "2.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_renaming_to_an_existing_name_is_rejected(self):
        other = Medication.objects.create(clinic=self.clinic, name="Ibuprofène", unit="boîte")
        url = reverse("medication-detail", args=[other.id])
        response = self.client.patch(url, {"name": "Doliprane"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "name")

    def test_max_threshold_below_min_threshold_is_rejected(self):
        payload = {"name": "Amoxicilline", "unit": "boîte", "min_threshold": 10, "max_threshold": 5}
        response = self.client.post(reverse("medication-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "max_threshold")
        self.assertEqual(response.data["message"], "Le seuil maximal doit être supérieur ou égal au seuil minimal.")

    def test_partial_update_checks_thresholds_against_stored_values(self):
        self.existing.min_threshold = 10
        self.existing.save(update_fields=["min_threshold"])
        url = reverse("medication-detail", args=[self.existing.id])
        response = self.client.patch(url, {"max_threshold": 5}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "max_threshold")


class StockBatchApiTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.pharmacist = create_user(clinic=self.clinic, role="pharmacist")
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.medication = Medication.objects.create(clinic=self.clinic, name="Doliprane", unit="boîte")

    def test_pharmacist_can_receive_a_batch(self):
        self.client.force_authenticate(self.pharmacist)
        payload = {
            "medication": self.medication.id,
            "batch_number": "LOT-001",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "received_date": date.today().isoformat(),
            "quantity": 40,
            "unit_cost": "1.20",
        }
        response = self.client.post(reverse("stock-batch-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.medication.refresh_from_db()
        self.assertEqual(self.medication.current_stock, 40)

    def test_negative_unit_cost_is_rejected(self):
        self.client.force_authenticate(self.pharmacist)
        payload = {
            "medication": self.medication.id,
            "batch_number": "LOT-002",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "received_date": date.today().isoformat(),
            "quantity": 10,
            "unit_cost": "-0.50",
        }
        response = self.client.post(reverse("stock-batch-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "unit_cost")
        self.medication.refresh_from_db()
        self.assertEqual(self.medication.current_stock, 0)

    def test_secretary_cannot_receive_a_batch(self):
        self.client.force_authenticate(self.secretary)
        payload = {
            "medication": self.medication.id,
            "batch_number": "LOT-001",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "received_date": date.today().isoformat(),
            "quantity": 40,
        }
        response = self.client.post(reverse("stock-batch-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
