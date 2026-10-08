"""Photos de profil (common/photos.py) : personnel et médecins (UserPhoto), patients (PatientPhoto,
avec consentement). Stockées en base, réduites, servies par URL signée qui expire."""

import io
from datetime import date
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import UserPhoto
from common import photos
from common.models import AuditLog
from common.testing import create_clinic, create_user
from doctors.models import Doctor
from patients.models import Patient, PatientPhoto


def _upload(image_format="PNG", size=(600, 400), mode="RGB", filename="photo.png"):
    buffer = io.BytesIO()
    Image.new(mode, size, color="blue").save(buffer, format=image_format)
    return SimpleUploadedFile(filename, buffer.getvalue(), content_type="application/octet-stream")


def _patient(clinic, number="PAT-1"):
    return Patient.objects.create(
        clinic=clinic, patient_number=number, first_name="Alice", last_name="Martin", phone="0600000001",
        date_of_birth=date(1990, 1, 1), gender=Patient.Gender.FEMALE,
    )


class StaffPhotoTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Photo")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.nurse = create_user(clinic=self.clinic, role="nurse")
        self.anonymous = APIClient()

    def test_staff_member_sets_own_photo_resized_to_a_jpeg_square(self):
        self.client.force_authenticate(self.nurse)
        response = self.client.post(reverse("me-photo"), {"photo": _upload()}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        stored = Image.open(io.BytesIO(bytes(UserPhoto.objects.get(user=self.nurse).content)))
        self.assertEqual((stored.format, stored.size), ("JPEG", (photos.PHOTO_SIZE, photos.PHOTO_SIZE)))

        url = response.data["photo"]
        self.assertTrue(url.startswith("http://testserver/api/v1/photos/"))
        served = self.anonymous.get(url)  # balise <img> : aucun en-tête d'authentification
        self.assertEqual((served.status_code, served["Content-Type"]), (200, "image/jpeg"))
        self.assertTrue(served["Cache-Control"].startswith("private"))
        self.assertEqual(self.client.get(reverse("me")).data["photo"], url)
        self.assertTrue(
            AuditLog.objects.filter(user=self.nurse, model_name="User", metadata={"photo": "set"}).exists()
        )

    def test_transparent_png_is_accepted(self):
        self.client.force_authenticate(self.nurse)
        response = self.client.post(reverse("me-photo"), {"photo": _upload(mode="RGBA")}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_other_formats_and_large_files_are_rejected(self):
        self.client.force_authenticate(self.nurse)
        bmp = self.client.post(reverse("me-photo"), {"photo": _upload("BMP", filename="p.bmp")}, format="multipart")
        self.assertEqual(bmp.status_code, status.HTTP_400_BAD_REQUEST)
        with mock.patch("common.images.MAX_IMAGE_SIZE_BYTES", 10):
            large = self.client.post(reverse("me-photo"), {"photo": _upload()}, format="multipart")
        self.assertEqual(large.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(UserPhoto.objects.exists())

    def test_removing_the_photo_deletes_it_and_retires_its_url(self):
        self.client.force_authenticate(self.nurse)
        url = self.client.post(reverse("me-photo"), {"photo": _upload()}, format="multipart").data["photo"]
        response = self.client.delete(reverse("me-photo"))
        self.assertIsNone(response.data["photo"])
        self.assertFalse(UserPhoto.objects.exists())
        self.assertEqual(self.anonymous.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_a_patient_account_has_no_account_photo(self):
        patient_user = create_user(clinic=self.clinic, role="patient")
        self.client.force_authenticate(patient_user)
        response = self.client.post(reverse("me-photo"), {"photo": _upload()}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_sets_a_doctor_photo_shown_in_the_doctor_and_staff_lists(self):
        doctor_user = create_user(clinic=self.clinic, role="doctor")
        Doctor.objects.create(user=doctor_user, clinic=self.clinic, professional_number="D1", specialty="General")
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("staff-photo", args=[doctor_user.id]), {"photo": _upload()}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        doctors = self.client.get(reverse("doctor-list")).data["results"]
        self.assertEqual(doctors[0]["user"]["photo"], response.data["photo"])
        staff = {member["id"]: member["photo"] for member in self.client.get(reverse("staff-list")).data["results"]}
        self.assertEqual(staff[doctor_user.id], response.data["photo"])
        self.assertIsNone(staff[self.nurse.id])

    def test_only_the_admin_changes_someone_else_photo(self):
        self.client.force_authenticate(self.nurse)
        response = self.client.post(reverse("staff-photo", args=[self.admin.id]), {"photo": _upload()}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cannot_change_a_photo_in_another_clinic(self):
        other = create_user(clinic=create_clinic("Autre"), role="nurse")
        self.client.force_authenticate(self.admin)
        response = self.client.post(reverse("staff-photo", args=[other.id]), {"photo": _upload()}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class PatientPhotoTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Patients")
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.patient = _patient(self.clinic)
        self.url = reverse("patient-photo", args=[self.patient.id])

    def test_secretary_sets_photo_with_patient_consent(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.post(self.url, {"photo": _upload(), "consent": True}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        photo = PatientPhoto.objects.get(patient=self.patient)
        self.assertEqual(photo.consent_recorded_by, self.secretary)
        self.assertIsNotNone(photo.consented_at)

        listed = self.client.get(reverse("patient-list")).data["results"][0]
        self.assertEqual(listed["photo"], response.data["photo"])
        self.assertEqual(APIClient().get(listed["photo"]).status_code, status.HTTP_200_OK)

    def test_photo_requires_consent(self):
        self.client.force_authenticate(self.secretary)
        for data in ({"photo": _upload()}, {"photo": _upload(), "consent": False}):
            response = self.client.post(self.url, data, format="multipart")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PatientPhoto.objects.exists())

    def test_secretary_can_remove_the_photo(self):
        self.client.force_authenticate(self.secretary)
        self.client.post(self.url, {"photo": _upload(), "consent": True}, format="multipart")
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["photo"])
        self.assertFalse(PatientPhoto.objects.exists())

    def test_doctor_sees_the_photo_but_cannot_change_it(self):
        doctor = create_user(clinic=self.clinic, role="doctor")
        self.client.force_authenticate(doctor)
        response = self.client.post(self.url, {"photo": _upload(), "consent": True}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_clinic_cannot_change_the_photo(self):
        self.client.force_authenticate(create_user(clinic=create_clinic("Autre"), role="secretary"))
        response = self.client.post(self.url, {"photo": _upload(), "consent": True}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class PhotoUrlTests(APITestCase):
    def setUp(self):
        clinic = create_clinic("Clinique URL")
        self.user = create_user(clinic=clinic, role="nurse")
        self.client.force_authenticate(self.user)
        self.url = self.client.post(reverse("me-photo"), {"photo": _upload()}, format="multipart").data["photo"]
        self.anonymous = APIClient()

    def test_url_expires(self):
        with mock.patch("common.photos.time.time", return_value=10**12):
            self.assertEqual(self.anonymous.get(self.url).status_code, status.HTTP_404_NOT_FOUND)

    def test_tampered_token_is_rejected(self):
        self.assertEqual(self.anonymous.get(self.url[:-3] + "abc/").status_code, status.HTTP_404_NOT_FOUND)

    def test_replacing_the_photo_retires_the_old_url(self):
        with mock.patch("common.photos._version", return_value=1):
            old_url = self.client.get(reverse("me")).data["photo"]
        self.assertEqual(self.anonymous.get(old_url).status_code, status.HTTP_404_NOT_FOUND)
