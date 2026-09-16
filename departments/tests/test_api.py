from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from departments.models import Department


class DepartmentTests(APITestCase):
    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.admin_a = create_user(clinic=self.clinic_a, role="clinic_admin")
        self.secretary_a = create_user(clinic=self.clinic_a, role="secretary")
        self.admin_b = create_user(clinic=self.clinic_b, role="clinic_admin")
        self.department_a = Department.objects.create(
            clinic=self.clinic_a, name="Cardiology", code="CARD", department_type=Department.DepartmentType.MEDICAL
        )

    def test_clinic_b_cannot_see_clinic_a_department(self):
        self.client.force_authenticate(self.admin_b)
        response = self.client.get(reverse("department-list"))
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(self.department_a.id, ids)

    def test_secretary_cannot_create_department(self):
        self.client.force_authenticate(self.secretary_a)
        response = self.client.post(
            reverse("department-list"), {"name": "Radiology", "code": "RAD", "department_type": "medical"}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_clinic_admin_can_create_department(self):
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(
            reverse("department-list"), {"name": "Radiology", "code": "RAD", "department_type": "medical"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "active")

    def test_delete_verb_is_not_allowed(self):
        # La suppression physique est interdite dans tout le système (business/workflow-policy.md) ;
        # les départements utilisent à la place les actions dédiées archive/restore, donc DELETE
        # n'est pas exposé du tout.
        self.client.force_authenticate(self.admin_a)
        response = self.client.delete(reverse("department-detail", args=[self.department_a.id]))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_archive_sets_status_and_is_active(self):
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(reverse("department-archive", args=[self.department_a.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.department_a.refresh_from_db()
        self.assertEqual(self.department_a.status, Department.Status.ARCHIVED)
        self.assertFalse(self.department_a.is_active)

    def test_archive_blocked_when_active_doctors_assigned(self):
        from common.testing import create_user as create_staff_user
        from doctors.models import Doctor

        doctor_user = create_staff_user(clinic=self.clinic_a, role="doctor")
        Doctor.objects.create(
            user=doctor_user, clinic=self.clinic_a, department=self.department_a, professional_number="D-001"
        )
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(reverse("department-archive", args=[self.department_a.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.department_a.refresh_from_db()
        self.assertEqual(self.department_a.status, Department.Status.ACTIVE)

    def test_restore_reactivates_archived_department(self):
        self.client.force_authenticate(self.admin_a)
        self.client.post(reverse("department-archive", args=[self.department_a.id]))
        response = self.client.post(reverse("department-restore", args=[self.department_a.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.department_a.refresh_from_db()
        self.assertEqual(self.department_a.status, Department.Status.ACTIVE)
        self.assertTrue(self.department_a.is_active)

    def test_archived_department_is_read_only(self):
        self.client.force_authenticate(self.admin_a)
        self.client.post(reverse("department-archive", args=[self.department_a.id]))
        response = self.client.patch(
            reverse("department-detail", args=[self.department_a.id]), {"name": "New Name"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deactivate_then_activate(self):
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(reverse("department-deactivate", args=[self.department_a.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.department_a.refresh_from_db()
        self.assertEqual(self.department_a.status, Department.Status.INACTIVE)

        response = self.client.post(reverse("department-activate", args=[self.department_a.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.department_a.refresh_from_db()
        self.assertEqual(self.department_a.status, Department.Status.ACTIVE)
