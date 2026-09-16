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
            "quantity_received": 40,
            "unit_cost": "1.20",
        }
        response = self.client.post(reverse("stock-batch-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.medication.refresh_from_db()
        self.assertEqual(self.medication.current_stock, 40)

    def test_secretary_cannot_receive_a_batch(self):
        self.client.force_authenticate(self.secretary)
        payload = {
            "medication": self.medication.id,
            "batch_number": "LOT-001",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "received_date": date.today().isoformat(),
            "quantity_received": 40,
        }
        response = self.client.post(reverse("stock-batch-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
