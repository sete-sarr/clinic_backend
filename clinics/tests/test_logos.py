"""Logos de clinique stockés en base et servis par URL signée (clinics/services.py) — ils doivent
survivre à un hébergement sans disque persistant ni diffusion de /media/, et apparaître dans les PDF."""

import io
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from clinics.models import ClinicLogo
from clinics.services import print_logo_data_uri
from common.testing import create_clinic, create_user


def _image_bytes(image_format, color="blue"):
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), color=color).save(buffer, format=image_format)
    return buffer.getvalue()


def _upload(content, filename):
    return SimpleUploadedFile(filename, content, content_type="application/octet-stream")


class ClinicLogoApiTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Logo")
        self.client.force_authenticate(create_user(clinic=self.clinic, role="clinic_admin"))
        self.url = reverse("clinic-detail", args=[self.clinic.id])
        self.anonymous = APIClient()

    def _patch_logo(self, field, content, filename="logo.png"):
        return self.client.patch(self.url, {field: _upload(content, filename)}, format="multipart")

    def test_uploaded_logo_is_stored_in_database_and_served_by_signed_url(self):
        content = _image_bytes("PNG")
        response = self._patch_logo("logo_light", content)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        logo = ClinicLogo.objects.get(clinic=self.clinic, kind=ClinicLogo.Kind.LIGHT)
        self.assertEqual((bytes(logo.content), logo.content_type), (content, "image/png"))

        url = response.data["logo_light"]
        self.assertIn("/api/v1/clinics/logos/", url)
        served = self.anonymous.get(url)  # balise <img> : aucun en-tête d'authentification
        self.assertEqual(served.status_code, status.HTTP_200_OK)
        self.assertEqual(served.content, content)
        self.assertEqual(served["Content-Type"], "image/png")
        self.assertIn("immutable", served["Cache-Control"])
        self.assertIsNone(response.data["logo_dark"])

    def test_jpeg_logo_keeps_its_content_type(self):
        response = self._patch_logo("logo_print", _image_bytes("JPEG"), "logo.jpg")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(self.anonymous.get(response.data["logo_print"])["Content-Type"], "image/jpeg")

    def test_tampered_token_is_rejected(self):
        url = self._patch_logo("logo_light", _image_bytes("PNG")).data["logo_light"]
        tampered = url.rstrip("/")[:-2] + "xx/"
        self.assertEqual(self.anonymous.get(tampered).status_code, status.HTTP_404_NOT_FOUND)

    def test_replacing_a_logo_changes_its_url_and_retires_the_old_one(self):
        self._patch_logo("logo_light", _image_bytes("PNG", "blue"))
        # Version antérieure garantie (deux envois peuvent tomber dans la même milliseconde).
        ClinicLogo.objects.filter(clinic=self.clinic).update(updated_at="2020-01-01T00:00:00Z")
        old_url = self.client.get(self.url).data["logo_light"]
        new_content = _image_bytes("PNG", "red")
        new_url = self._patch_logo("logo_light", new_content).data["logo_light"]

        self.assertNotEqual(old_url, new_url)
        self.assertEqual(self.anonymous.get(old_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.anonymous.get(new_url).content, new_content)

    def test_null_removes_the_logo(self):
        self._patch_logo("logo_light", _image_bytes("PNG"))
        response = self.client.patch(self.url, {"logo_light": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data["logo_light"])
        self.assertFalse(ClinicLogo.objects.filter(clinic=self.clinic).exists())

    def test_other_staff_see_the_logo_but_cannot_change_it(self):
        url = self._patch_logo("logo_light", _image_bytes("PNG")).data["logo_light"]
        self.client.force_authenticate(create_user(clinic=self.clinic, role="secretary"))
        self.assertEqual(self.client.get(self.url).data["logo_light"], url)
        self.assertEqual(self._patch_logo("logo_light", _image_bytes("PNG")).status_code, status.HTTP_403_FORBIDDEN)


class PrintLogoTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique PDF")

    def _logo(self, kind, image_format="PNG"):
        content = _image_bytes(image_format)
        ClinicLogo.objects.create(clinic=self.clinic, kind=kind, content=content,
                                  content_type="image/png" if image_format == "PNG" else "image/jpeg")
        return content

    def test_print_logo_is_preferred_then_main_logo_then_nothing(self):
        self.assertEqual(print_logo_data_uri(self.clinic), "")
        self._logo(ClinicLogo.Kind.LIGHT, "PNG")
        self.assertTrue(print_logo_data_uri(self.clinic).startswith("data:image/png;base64,"))
        self._logo(ClinicLogo.Kind.PRINT, "JPEG")
        self.assertTrue(print_logo_data_uri(self.clinic).startswith("data:image/jpeg;base64,"))

    def test_pdf_templates_embed_the_logo(self):
        self._logo(ClinicLogo.Kind.LIGHT)
        document = render_to_string("pdf/base_document.html", {"clinic": self.clinic})
        ticket = render_to_string("appointments/checkin_ticket_pdf.html", {"appointment": SimpleNamespace(clinic=self.clinic)})
        for html in (document, ticket):
            self.assertIn('src="data:image/png;base64,', html)

    def test_pdf_falls_back_to_the_clinic_initial_without_logo(self):
        document = render_to_string("pdf/base_document.html", {"clinic": self.clinic})
        self.assertNotIn("data:image", document)
        self.assertIn('class="clinic-logo-fallback">C<', document)
