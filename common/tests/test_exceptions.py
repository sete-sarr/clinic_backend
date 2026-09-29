"""Forme {code, message, field} des erreurs et langue du message (docs/i18n.md §4)."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user


class NotFoundMessageTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(create_user(clinic=create_clinic(), role="clinic_admin"))
        self.url = reverse("patient-detail", args=[999999])

    def test_missing_object_message_is_translated_and_generic(self):
        for language, message in (("fr", "Élément introuvable."), ("en", "Item not found.")):
            with self.subTest(language=language):
                response = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE=language)
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(response.data, {"code": 404, "message": message, "field": None})
