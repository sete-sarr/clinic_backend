"""Plateforme bilingue (docs/i18n.md) : catalogue anglais complet et à jour, langue de réponse de
l'API choisie par l'en-tête Accept-Language, préférence de langue de l'utilisateur."""

from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from common.translation_catalog import build_mo, extract_msgids, read_po
from pharmacy.models import Medication

EN_PO = Path(settings.BASE_DIR) / "locale" / "en" / "LC_MESSAGES" / "django.po"


class EnglishCatalogTests(SimpleTestCase):
    def test_every_translatable_message_has_an_english_translation(self):
        msgids = extract_msgids(Path(settings.BASE_DIR))
        catalog = read_po(EN_PO)
        missing = sorted(m for m in msgids if not catalog.get(m))
        self.assertEqual(missing, [], "Messages sans traduction anglaise dans locale/en/LC_MESSAGES/django.po")

    def test_catalog_has_no_obsolete_entries(self):
        msgids = extract_msgids(Path(settings.BASE_DIR))
        catalog = read_po(EN_PO)
        obsolete = sorted(m for m in catalog if m and m not in msgids)
        self.assertEqual(obsolete, [], "Entrées du .po qui ne correspondent plus à aucun message du code")

    def test_compiled_mo_is_up_to_date(self):
        expected = build_mo(read_po(EN_PO))
        self.assertEqual(
            EN_PO.with_suffix(".mo").read_bytes(), expected,
            "django.mo n'est pas à jour : lancer `python manage.py compile_translations`",
        )


class ApiResponseLanguageTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.client.force_authenticate(create_user(clinic=self.clinic, role="pharmacist"))
        Medication.objects.create(clinic=self.clinic, name="Doliprane", unit="boîte")
        self.url = reverse("medication-list")
        self.duplicate = {"name": "Doliprane", "unit": "boîte"}

    def test_french_by_default(self):
        response = self.client.post(self.url, self.duplicate, format="json")
        self.assertEqual(response.data["message"], "Un médicament portant ce nom existe déjà dans cette clinique.")

    def test_english_when_requested(self):
        response = self.client.post(self.url, self.duplicate, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["message"], "A medication with this name already exists in this clinic.")

    def test_django_builtin_messages_follow_the_language(self):
        response = self.client.post(self.url, {"unit": "boîte"}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.data["message"], "This field is required.")
        response = self.client.post(self.url, {"unit": "boîte"}, format="json", HTTP_ACCEPT_LANGUAGE="fr")
        self.assertEqual(response.data["message"], "Ce champ est obligatoire.")

    def test_interpolated_message_is_translated(self):
        self.clinic.subscription_status = "suspended"
        self.clinic.save(update_fields=["subscription_status"])
        response = self.client.post(self.url, {"name": "Autre", "unit": "boîte"}, format="json", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(response.data["message"].startswith("This clinic's subscription is suspended."))


class UserLanguagePreferenceTests(APITestCase):
    def setUp(self):
        cache.clear()  # la connexion est soumise à ScopedRateThrottle
        self.clinic = create_clinic()
        self.user = create_user(clinic=self.clinic, role="secretary", username="sec1")

    def test_preference_defaults_to_empty_and_effective_language_follows_clinic(self):
        self.assertEqual(self.user.language, "")
        self.assertEqual(self.user.effective_language, "fr")
        self.clinic.locale = "en"
        self.clinic.save(update_fields=["locale"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.effective_language, "en")

    def test_user_can_set_own_language(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(reverse("me"), {"language": "en"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["language"], "en")
        self.user.refresh_from_db()
        self.assertEqual(self.user.effective_language, "en")

    def test_unsupported_language_is_rejected(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(reverse("me"), {"language": "de"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["field"], "language")

    def test_only_language_can_be_changed_through_me(self):
        self.client.force_authenticate(self.user)
        self.client.patch(reverse("me"), {"language": "en", "email": "hijack@example.com", "clinic": 999}, format="json")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "sec1@example.com")
        self.assertEqual(self.user.clinic_id, self.clinic.id)

    def test_login_response_includes_language(self):
        self.user.language = "en"
        self.user.save(update_fields=["language"])
        response = self.client.post(
            reverse("token_obtain_pair"), {"email": "sec1@example.com", "password": "pass1234!"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["language"], "en")
