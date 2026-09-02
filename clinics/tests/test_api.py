import io

from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from clinics.models import Clinic
from common.models import AuditLog
from common.testing import create_clinic, create_user


def _generate_image_upload(*, image_format, filename):
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="blue").save(buffer, format=image_format)
    buffer.seek(0)
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(filename, buffer.read(), content_type=f"image/{image_format.lower()}")


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

    def test_update_records_audit_log(self):
        self.client.force_authenticate(self.clinic_admin)
        response = self.client.patch(reverse("clinic-detail", args=[self.clinic.id]), {"address": "1 Rue Test"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.UPDATE, model_name="Clinic", object_id=str(self.clinic.id)
            ).exists()
        )


class ClinicLogoUploadTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.clinic_admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.client.force_authenticate(self.clinic_admin)

    def test_png_logo_is_accepted(self):
        upload = _generate_image_upload(image_format="PNG", filename="logo.png")
        response = self.client.patch(
            reverse("clinic-detail", args=[self.clinic.id]), {"logo_light": upload}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_jpeg_logo_is_accepted(self):
        upload = _generate_image_upload(image_format="JPEG", filename="logo.jpg")
        response = self.client.patch(
            reverse("clinic-detail", args=[self.clinic.id]), {"logo_light": upload}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_bmp_logo_is_rejected(self):
        # security audit, 2026-09-02: only PNG/JPEG are accepted per design-system/.
        upload = _generate_image_upload(image_format="BMP", filename="logo.bmp")
        response = self.client.patch(
            reverse("clinic-detail", args=[self.clinic.id]), {"logo_light": upload}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
