"""Registre des visiteurs (docs/visitors.md) : entrée / sortie, doublons, personne visitée,
correction le jour même, clôture automatique, purge à 1 an, export administrateur, isolation."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from common.models import AuditLog
from common.testing import create_clinic, create_user
from departments.models import Department
from doctors.models import Doctor
from hospitalization.models import Admission, Bed, Room, RoomType
from hospitalization.services import create_admission
from patients.models import Patient
from visitors.models import VisitLog
from visitors.services import auto_close_open_visits, purge_expired_visits


class VisitorRegistryTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinic A")
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.client.force_authenticate(self.secretary)

    def _check_in(self, **extra):
        data = {"visitor_name": "Mamadou Sy", "visitor_phone": "770000000", "visitor_type": "supplier",
                "purpose": "Livraison de matériel", **extra}
        return self.client.post(reverse("visit-list"), data, format="json")

    def _admitted_stay(self):
        self.clinic.inpatient_nightly_rate = Decimal("1000")
        self.clinic.save(update_fields=["inpatient_nightly_rate"])
        department = Department.objects.create(clinic=self.clinic, name="Médecine", code="MED", department_type="medical")
        room_type = RoomType.objects.create(clinic=self.clinic, name="Double", nightly_rate=Decimal("1000"))
        room = Room.objects.create(clinic=self.clinic, department=department, number="12", room_type=room_type)
        bed = Bed.objects.create(clinic=self.clinic, room=room, label="A")
        doctor_user = create_user(clinic=self.clinic, role="doctor")
        doctor = Doctor.objects.create(user=doctor_user, clinic=self.clinic, professional_number="D1", specialty="G")
        patient = Patient.objects.create(
            clinic=self.clinic, patient_number="PAT-1", first_name="Grace", last_name="Hopper", phone="1",
            date_of_birth=date(1990, 1, 1), gender=Patient.Gender.FEMALE,
        )
        return create_admission(clinic=self.clinic, doctor=doctor, patient=patient, department=department,
                                reason="Motif confidentiel", actor=doctor_user, bed=bed)

    def test_check_in_and_check_out(self):
        response = self._check_in()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["is_present"])
        present = self.client.get(reverse("visit-list"), {"present": "true"}).data
        self.assertEqual(present["count"], 1)
        out = self.client.post(reverse("visit-check-out", args=[response.data["id"]]))
        self.assertEqual(out.status_code, 200)
        self.assertFalse(out.data["is_present"])
        self.assertGreater(out.data["checked_out_at"], out.data["checked_in_at"])
        self.assertEqual(self.client.post(reverse("visit-check-out", args=[response.data["id"]])).status_code, 400)

    def test_same_person_cannot_be_present_twice(self):
        self._check_in()
        self.assertEqual(self._check_in(visitor_name="mamadou sy").status_code, 400)
        self.assertEqual(self._check_in(visitor_phone="771111111").status_code, 201)

    def test_required_fields(self):
        self.assertEqual(self._check_in(visitor_name=" ").status_code, 400)
        self.assertEqual(self._check_in(purpose="").status_code, 400)

    def test_visit_to_admitted_patient_shows_location_never_reason(self):
        stay = self._admitted_stay()
        targets = self.client.get(reverse("visit-targets"), {"search": "Grace"}).data
        self.assertEqual(targets["admissions"][0]["id"], stay.id)
        self.assertIn("12 — A", targets["admissions"][0]["label"])
        self.assertNotIn("Motif", str(targets))
        response = self._check_in(visitor_type="patient_visit", visited_admission=stay.id)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("Grace Hopper", response.data["visited_display"])
        self.assertNotIn("Motif", response.data["visited_display"])

    def test_only_one_target_and_only_admitted_stays(self):
        stay = self._admitted_stay()
        both = self._check_in(visited_admission=stay.id, visited_free_text="Comptabilité")
        self.assertEqual(both.status_code, 400)
        Admission.objects.filter(pk=stay.pk).update(status="discharged")
        self.assertEqual(self._check_in(visited_admission=stay.id).status_code, 400)

    def test_correction_only_same_day(self):
        visit_id = self._check_in().data["id"]
        url = reverse("visit-detail", args=[visit_id])
        self.assertEqual(self.client.patch(url, {"purpose": "Maintenance"}, format="json").status_code, 200)
        VisitLog.objects.filter(pk=visit_id).update(checked_in_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.client.patch(url, {"purpose": "X"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete(url).status_code, 405)

    def test_auto_close_previous_days_only(self):
        old = VisitLog.objects.create(
            clinic=self.clinic, visitor_name="A", visitor_type="other", purpose="X",
            checked_in_at=timezone.make_aware(datetime(2026, 10, 6, 15, 0), timezone.get_current_timezone()),
        )
        today = VisitLog.objects.create(
            clinic=self.clinic, visitor_name="B", visitor_type="other", purpose="X",
            checked_in_at=timezone.make_aware(datetime(2026, 10, 7, 9, 0), timezone.get_current_timezone()),
        )
        now = timezone.make_aware(datetime(2026, 10, 7, 10, 0), timezone.get_current_timezone())
        self.assertEqual(auto_close_open_visits(now), 1)
        old.refresh_from_db()
        today.refresh_from_db()
        self.assertTrue(old.auto_closed)
        self.assertEqual(timezone.localtime(old.checked_out_at).hour, 23)
        self.assertIsNone(today.checked_out_at)

    def test_purge_after_one_year(self):
        recent = VisitLog.objects.create(clinic=self.clinic, visitor_name="A", visitor_type="other", purpose="X",
                                         checked_in_at=timezone.now() - timedelta(days=300))
        VisitLog.objects.create(clinic=self.clinic, visitor_name="B", visitor_type="other", purpose="X",
                                checked_in_at=timezone.now() - timedelta(days=400))
        self.assertEqual(purge_expired_visits(), 1)
        self.assertEqual(list(VisitLog.objects.values_list("pk", flat=True)), [recent.pk])

    def test_export_reserved_to_admin_and_audited(self):
        self._check_in()
        self.assertEqual(self.client.get(reverse("visit-export-csv")).status_code, 403)
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("visit-export-csv"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Mamadou Sy", response.content.decode())
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.EXPORT, model_name="visit").exists())

    def test_clinical_and_finance_roles_have_no_access(self):
        for role in ("doctor", "nurse", "accountant", "lab_technician"):
            self.client.force_authenticate(create_user(clinic=self.clinic, role=role))
            self.assertEqual(self.client.get(reverse("visit-list")).status_code, 403)

    def test_other_clinic_isolated(self):
        visit_id = self._check_in().data["id"]
        self.client.force_authenticate(create_user(clinic=create_clinic("Clinic B"), role="secretary"))
        self.assertEqual(self.client.get(reverse("visit-list")).data["count"], 0)
        self.assertEqual(self.client.post(reverse("visit-check-out", args=[visit_id])).status_code, 404)
