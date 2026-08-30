from datetime import date

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user


class ClinicActivityReportTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.clinic_admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.secretary = create_user(clinic=self.clinic, role="secretary")

    def test_clinic_admin_can_generate_report(self):
        self.client.force_authenticate(self.clinic_admin)
        response = self.client.get(reverse("clinic-activity-report"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_clinic_admin_can_generate_report_with_date_range(self):
        self.client.force_authenticate(self.clinic_admin)
        response = self.client.get(
            reverse("clinic-activity-report"),
            {"date_from": date(2026, 1, 1).isoformat(), "date_to": date(2026, 12, 31).isoformat()},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_secretary_cannot_generate_report(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.get(reverse("clinic-activity-report"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_records_audit_log(self):
        from common.models import AuditLog

        self.client.force_authenticate(self.clinic_admin)
        self.client.get(reverse("clinic-activity-report"))
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.PRINT, model_name="Clinic").exists())
