from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from common.models import AuditLog
from common.testing import create_clinic, create_user


class StaffTenantIsolationTests(APITestCase):
    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.admin_a = create_user(clinic=self.clinic_a, role="clinic_admin")
        self.admin_b = create_user(clinic=self.clinic_b, role="clinic_admin")
        self.secretary_a = create_user(clinic=self.clinic_a, role="secretary")

    def test_clinic_b_admin_cannot_see_clinic_a_staff(self):
        self.client.force_authenticate(self.admin_b)
        response = self.client.get(reverse("staff-list"))
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(self.secretary_a.id, ids)

    def test_clinic_b_admin_cannot_retrieve_clinic_a_staff_member(self):
        self.client.force_authenticate(self.admin_b)
        response = self.client.get(reverse("staff-detail", args=[self.secretary_a.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_clinic_b_admin_cannot_deactivate_clinic_a_staff_member(self):
        self.client.force_authenticate(self.admin_b)
        response = self.client.post(reverse("staff-deactivate", args=[self.secretary_a.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class StaffPermissionTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinic")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.doctor = create_user(clinic=self.clinic, role="doctor")
        self.accountant = create_user(clinic=self.clinic, role="accountant")

    def test_secretary_cannot_list_staff(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.get(reverse("staff-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_doctor_cannot_create_staff(self):
        self.client.force_authenticate(self.doctor)
        response = self.client.post(reverse("staff-list"), self._payload())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_accountant_cannot_deactivate_staff(self):
        self.client.force_authenticate(self.accountant)
        response = self.client.post(reverse("staff-deactivate", args=[self.secretary.id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def _payload(self, **overrides):
        payload = {
            "username": "new.staff",
            "email": "staff@example.com",
            "first_name": "New",
            "last_name": "Staff",
            "password": "S3curePass!23",
            "role": "secretary",
        }
        payload.update(overrides)
        return payload

    def test_clinic_admin_can_create_secretary(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(reverse("staff-list"), self._payload(role="secretary"))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["role"], "secretary")

    def test_clinic_admin_can_create_accountant(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("staff-list"), self._payload(username="new.accountant", role="accountant")
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["role"], "accountant")

    def test_clinic_admin_can_create_another_clinic_admin(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("staff-list"), self._payload(username="second.admin", role="clinic_admin")
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["role"], "clinic_admin")

    def test_same_username_in_a_different_clinic_is_allowed(self):
        # Usernames are unique per clinic (User.Meta.constraints), not globally — an unrelated
        # clinic already using "new.staff" (see other tests in this class) must never block this
        # clinic from using the same username for its own staff member.
        other_clinic = create_clinic("Other Clinic")
        create_user(clinic=other_clinic, username="cross.clinic.name", role="secretary")
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("staff-list"), self._payload(username="cross.clinic.name", email="cross@example.com")
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_same_username_within_the_same_clinic_is_rejected(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("staff-list"), self._payload(username=self.secretary.username, email="another@example.com")
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_email_across_clinics_is_rejected(self):
        # email is the global login identifier (User.USERNAME_FIELD) — unique across all clinics.
        other_clinic = create_clinic("Other Clinic")
        other_user = create_user(clinic=other_clinic, role="secretary")
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("staff-list"), self._payload(username="fresh.username", email=other_user.email)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_staff_with_doctor_role_is_rejected(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(reverse("staff-list"), self._payload(username="not.a.doctor", role="doctor"))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patient_role_user_excluded_from_staff_list(self):
        patient_user = create_user(clinic=self.clinic, role="patient")
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("staff-list"))
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(patient_user.id, ids)


class StaffBusinessRuleTests(APITestCase):
    def setUp(self):
        cache.clear()  # login is now ScopedRateThrottle'd (security audit, 2026-09-02).
        self.clinic = create_clinic("Clinic")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.other_admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.secretary = create_user(clinic=self.clinic, role="secretary")

    def test_admin_cannot_deactivate_own_account(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(reverse("staff-deactivate", args=[self.admin.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deactivating_non_last_admin_succeeds(self):
        self.client.force_authenticate(self.admin)
        # Two active admins exist (self.admin, self.other_admin) — deactivating one is fine.
        response = self.client.post(reverse("staff-deactivate", args=[self.other_admin.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_cannot_deactivate_last_active_clinic_admin(self):
        from django.core.exceptions import ValidationError

        from accounts.services import deactivate_staff_member

        # A third account exists so the actor deactivating self.admin isn't blocked by the
        # self-deactivation rule instead — but the third account is NOT a clinic_admin, so it
        # doesn't count toward "remaining active admins".
        secretary_actor = create_user(clinic=self.clinic, role="secretary")
        # Deactivate other_admin first so self.admin becomes the clinic's only active clinic_admin.
        deactivate_staff_member(user=self.other_admin, actor=self.admin)
        with self.assertRaises(ValidationError):
            deactivate_staff_member(user=self.admin, actor=secretary_actor)

    def test_deactivated_user_cannot_authenticate(self):
        self.client.force_authenticate(self.admin)
        self.client.post(reverse("staff-deactivate", args=[self.secretary.id]))
        self.secretary.refresh_from_db()
        self.assertFalse(self.secretary.is_active)

        response = self.client.post(
            reverse("token_obtain_pair"), {"email": self.secretary.email, "password": "pass1234!"}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_reactivate_restores_login_capability(self):
        self.client.force_authenticate(self.admin)
        self.client.post(reverse("staff-deactivate", args=[self.secretary.id]))
        response = self.client.post(reverse("staff-reactivate", args=[self.secretary.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.secretary.refresh_from_db()
        self.assertTrue(self.secretary.is_active)

        login_response = self.client.post(
            reverse("token_obtain_pair"), {"email": self.secretary.email, "password": "pass1234!"}
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

    def test_role_change_updates_group_membership_and_logs_permission_change(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(reverse("staff-role", args=[self.secretary.id]), {"role": "accountant"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["role"], "accountant")
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.PERMISSION_CHANGE,
                model_name="User",
                object_id=str(self.secretary.id),
            ).exists()
        )

    def test_role_change_rejects_doctor_boundary_transition(self):
        doctor_user = create_user(clinic=self.clinic, role="doctor")
        self.client.force_authenticate(self.admin)
        response = self.client.post(reverse("staff-role", args=[doctor_user.id]), {"role": "secretary"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_staff_records_audit_create_and_permission_change(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("staff-list"),
            {
                "username": "audited.staff",
                "email": "audited@example.com",
                "first_name": "Audited",
                "last_name": "Staff",
                "password": "S3curePass!23",
                "role": "secretary",
            },
        )
        user_id = response.data["id"]
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.CREATE, model_name="User", object_id=str(user_id)).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.PERMISSION_CHANGE, model_name="User", object_id=str(user_id)
            ).exists()
        )

    def test_deactivate_records_audit_archive(self):
        self.client.force_authenticate(self.admin)
        self.client.post(reverse("staff-deactivate", args=[self.secretary.id]))
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ARCHIVE, model_name="User", object_id=str(self.secretary.id)
            ).exists()
        )
