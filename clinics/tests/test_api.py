from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from clinics.models import Clinic
from common.testing import create_clinic, create_user


class ClinicPublicListTests(APITestCase):
    def setUp(self):
        self.active_clinic = create_clinic("Sunrise Clinic")
        self.inactive_clinic = create_clinic("Closed Clinic")
        self.inactive_clinic.is_active = False
        self.inactive_clinic.save(update_fields=["is_active"])

    def test_unauthenticated_request_succeeds(self):
        response = self.client.get(reverse("clinic-public-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_only_returns_id_and_name(self):
        response = self.client.get(reverse("clinic-public-list"))
        item = next(r for r in response.data["results"] if r["id"] == self.active_clinic.id)
        self.assertEqual(set(item.keys()), {"id", "name"})

    def test_inactive_clinics_excluded(self):
        response = self.client.get(reverse("clinic-public-list"))
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.active_clinic.id, ids)
        self.assertNotIn(self.inactive_clinic.id, ids)

    def test_search_by_name(self):
        response = self.client.get(reverse("clinic-public-list"), {"search": "Sunrise"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(ids, [self.active_clinic.id])


class ClinicSubscriptionFieldsReadOnlyTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.clinic_admin = create_user(clinic=self.clinic, role="clinic_admin")

    def test_get_exposes_subscription_fields(self):
        self.client.force_authenticate(self.clinic_admin)
        response = self.client.get(reverse("clinic-detail", args=[self.clinic.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("subscription_status", response.data)
        self.assertIn("plan_tier", response.data)
        self.assertNotIn("stripe_customer_id", response.data)

    def test_patch_cannot_set_subscription_status(self):
        self.client.force_authenticate(self.clinic_admin)
        response = self.client.patch(
            reverse("clinic-detail", args=[self.clinic.id]), {"subscription_status": Clinic.SubscriptionStatus.ACTIVE}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.TRIAL)
