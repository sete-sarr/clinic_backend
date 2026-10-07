"""Pagination commune (common/pagination.py) : `page_size` est honoré pour les sélecteurs du
frontend, dans la limite de 200 lignes — auparavant ignoré (20 lignes quoi qu'il arrive)."""

from django.urls import reverse
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from departments.models import Department


class PageSizeTests(APITestCase):
    def setUp(self):
        clinic = create_clinic("Clinic A")
        Department.objects.bulk_create([
            Department(clinic=clinic, name=f"Service {i}", code=f"S{i}", department_type=Department.DepartmentType.MEDICAL)
            for i in range(25)
        ])
        self.client.force_authenticate(create_user(clinic=clinic, role="clinic_admin"))

    def test_default_and_requested_page_size(self):
        url = reverse("department-list")
        self.assertEqual(len(self.client.get(url).data["results"]), 20)
        self.assertEqual(len(self.client.get(url, {"page_size": 100}).data["results"]), 25)
        self.assertEqual(len(self.client.get(url, {"page_size": 5}).data["results"]), 5)
