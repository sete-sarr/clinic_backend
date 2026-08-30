from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from departments.models import Department
from doctors.models import Doctor


class DoctorTenantIsolationTests(APITestCase):
    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.admin_a = create_user(clinic=self.clinic_a, role="clinic_admin")
        self.admin_b = create_user(clinic=self.clinic_b, role="clinic_admin")
        doctor_user_a = create_user(clinic=self.clinic_a, role="doctor")
        self.doctor_a = Doctor.objects.create(
            user=doctor_user_a, clinic=self.clinic_a, professional_number="DOC-001", specialty="General"
        )

    def test_clinic_b_admin_cannot_see_clinic_a_doctor(self):
        self.client.force_authenticate(self.admin_b)
        response = self.client.get(reverse("doctor-list"))
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(self.doctor_a.id, ids)

    def test_clinic_admin_can_create_doctor_with_user_account(self):
        self.client.force_authenticate(self.admin_a)
        payload = {
            "username": "dr.jones",
            "email": "jones@example.com",
            "first_name": "John",
            "last_name": "Jones",
            "password": "S3curePass!23",
            "professional_number": "DOC-002",
            "specialty": "Pediatrics",
        }
        response = self.client.post(reverse("doctor-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["user"]["username"], "dr.jones")


class DoctorCrossClinicFKTests(APITestCase):
    """Regression coverage for the cross-tenant FK injection audit finding: a clinic_admin must not
    be able to attach their doctor to another clinic's department, on create or on update."""

    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.admin_a = create_user(clinic=self.clinic_a, role="clinic_admin")
        self.department_b = Department.objects.create(
            clinic=self.clinic_b, name="Cardiology", code="CARD", department_type=Department.DepartmentType.MEDICAL
        )
        self.client.force_authenticate(self.admin_a)

    def test_cannot_create_doctor_with_other_clinics_department(self):
        payload = {
            "username": "dr.smith",
            "email": "smith@example.com",
            "first_name": "Jane",
            "last_name": "Smith",
            "password": "S3curePass!23",
            "professional_number": "DOC-003",
            "specialty": "Cardiology",
            "department": self.department_b.id,
        }
        response = self.client.post(reverse("doctor-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_update_doctor_to_other_clinics_department(self):
        doctor_user = create_user(clinic=self.clinic_a, role="doctor")
        doctor = Doctor.objects.create(
            user=doctor_user, clinic=self.clinic_a, professional_number="DOC-004", specialty="General"
        )
        response = self.client.patch(
            reverse("doctor-detail", args=[doctor.id]), {"department": self.department_b.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        doctor.refresh_from_db()
        self.assertIsNone(doctor.department_id)
