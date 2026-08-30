from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.audit import record_audit
from common.models import AuditLog
from common.testing import create_clinic, create_user


class AuditLogAccessTests(APITestCase):
    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.admin_a = create_user(clinic=self.clinic_a, role="clinic_admin")
        self.secretary_a = create_user(clinic=self.clinic_a, role="secretary")
        self.admin_b = create_user(clinic=self.clinic_b, role="clinic_admin")

        self.log_a = record_audit(user=self.admin_a, action=AuditLog.Action.LOGIN, obj=self.admin_a)
        self.log_b = record_audit(user=self.admin_b, action=AuditLog.Action.LOGIN, obj=self.admin_b)

    def test_clinic_admin_only_sees_own_clinic_entries(self):
        self.client.force_authenticate(self.admin_a)
        response = self.client.get(reverse("audit-log-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.log_a.id, ids)
        self.assertNotIn(self.log_b.id, ids)

    def test_non_admin_role_forbidden(self):
        self.client.force_authenticate(self.secretary_a)
        response = self.client.get(reverse("audit-log-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_read_only_no_create(self):
        self.client.force_authenticate(self.admin_a)
        response = self.client.post(reverse("audit-log-list"), {"action": "create"})
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_filter_by_action(self):
        record_audit(user=self.admin_a, action=AuditLog.Action.LOGOUT, obj=self.admin_a)
        self.client.force_authenticate(self.admin_a)
        response = self.client.get(reverse("audit-log-list"), {"action": "logout"})
        actions = {item["action"] for item in response.data["results"]}
        self.assertEqual(actions, {"logout"})
