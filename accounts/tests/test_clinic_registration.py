from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from clinics.models import Clinic
from common.models import AuditLog


class ClinicRegistrationTests(APITestCase):
    def setUp(self):
        cache.clear()  # ScopedRateThrottle utilise le cache par défaut (partagé entre process).
        self.payload = {
            "clinic_name": "Riverside Clinic",
            "clinic_email": "contact@riverside.example.com",
            "clinic_phone": "0600000000",
            "username": "riverside_admin",
            "email": "admin@riverside.example.com",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "password": "S0me-Strong-Pass!",
        }

    def test_registration_creates_clinic_admin_and_starts_trial(self):
        response = self.client.post(reverse("clinic-registration"), self.payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        clinic = Clinic.objects.get(name="Riverside Clinic")
        self.assertEqual(clinic.subscription_status, Clinic.SubscriptionStatus.TRIAL)
        self.assertIsNotNone(clinic.trial_ends_at)

        user = User.objects.get(username="riverside_admin")
        self.assertEqual(user.clinic_id, clinic.id)
        self.assertIn("clinic_admin", list(user.groups.values_list("name", flat=True)))

    def test_registration_returns_usable_tokens(self):
        response = self.client.post(reverse("clinic-registration"), self.payload)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["username"], "riverside_admin")
        self.assertEqual(response.data["user"]["roles"], ["clinic_admin"])

        me_response = self.client.get(
            reverse("me"), HTTP_AUTHORIZATION=f"Bearer {response.data['access']}"
        )
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)

    def test_registration_records_audit_log(self):
        response = self.client.post(reverse("clinic-registration"), self.payload)
        user_id = response.data["user"]["id"]
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.CREATE, model_name="Clinic"
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.CREATE, model_name="User", object_id=str(user_id)
            ).exists()
        )

    def test_same_username_across_different_clinics_is_allowed(self):
        # Les usernames sont uniques par clinique (User.Meta.constraints), pas globalement — une
        # clinique sans rapport utilisant déjà "riverside_admin" ne doit jamais empêcher une toute
        # nouvelle clinique d'utiliser le même username pour son propre premier admin.
        self.client.post(reverse("clinic-registration"), self.payload)
        second_payload = {**self.payload, "clinic_name": "Other Clinic", "email": "other@example.com"}
        response = self.client.post(reverse("clinic-registration"), second_payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_duplicate_email_is_rejected(self):
        # email est l'identifiant de connexion global (User.USERNAME_FIELD) — unique sur toutes les cliniques.
        self.client.post(reverse("clinic-registration"), self.payload)
        second_payload = {**self.payload, "clinic_name": "Other Clinic", "username": "other_admin"}
        response = self.client.post(reverse("clinic-registration"), second_payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_clinic_name_is_rejected(self):
        # Le sélecteur public de clinique sur le portail patient (ClinicPublicSerializer) n'affiche
        # que le nom — deux cliniques portant le même nom seraient indiscernables pour un patient.
        self.client.post(reverse("clinic-registration"), self.payload)
        second_payload = {
            **self.payload, "username": "other_admin", "email": "other@example.com",
        }
        response = self.client.post(reverse("clinic-registration"), second_payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_clinic_name_is_rejected_case_and_whitespace_insensitively(self):
        self.client.post(reverse("clinic-registration"), self.payload)
        second_payload = {
            **self.payload,
            "clinic_name": "  riverside clinic  ",
            "username": "other_admin",
            "email": "other@example.com",
        }
        response = self.client.post(reverse("clinic-registration"), second_payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_weak_password_is_rejected(self):
        weak_payload = {**self.payload, "password": "123"}
        response = self.client.post(reverse("clinic-registration"), weak_payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_throttled_after_five_requests_per_hour(self):
        for i in range(5):
            payload = {
                **self.payload,
                "clinic_name": f"Clinic {i}",
                "username": f"admin{i}",
                "email": f"admin{i}@example.com",
            }
            response = self.client.post(reverse("clinic-registration"), payload)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        sixth_payload = {**self.payload, "clinic_name": "Clinic 6", "username": "admin6", "email": "admin6@example.com"}
        response = self.client.post(reverse("clinic-registration"), sixth_payload)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
