from datetime import date

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from common.models import AuditLog
from common.testing import create_clinic, create_user
from consultations.models import Consultation
from doctors.models import Doctor
from patients.models import Patient


def _create_doctor(clinic, username=None):
    user = create_user(clinic=clinic, role="doctor", username=username)
    return Doctor.objects.create(
        user=user, clinic=clinic, professional_number=f"DOC-{user.id}", specialty="General"
    )


def _create_patient(clinic, number="PAT-2026-00001"):
    return Patient.objects.create(
        clinic=clinic,
        patient_number=number,
        first_name="Test",
        last_name="Patient",
        phone="0600000000",
        date_of_birth=date(1990, 1, 1),
        gender=Patient.Gender.OTHER,
    )


class ConsultationOwnerIsolationTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor_a = _create_doctor(self.clinic, "doc_a")
        self.doctor_b = _create_doctor(self.clinic, "doc_b")
        self.patient = _create_patient(self.clinic)
        self.consultation = Consultation.objects.create(
            clinic=self.clinic, patient=self.patient, doctor=self.doctor_a, date=timezone.now()
        )

    def test_doctor_b_cannot_see_doctor_a_consultation(self):
        self.client.force_authenticate(self.doctor_b.user)
        response = self.client.get(reverse("consultation-list"))
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(self.consultation.id, ids)

    def test_doctor_b_gets_404_not_403_on_doctor_a_consultation_detail(self):
        # docs/known-issues.md #1: the consultation is outside this doctor's queryset entirely, so
        # it 404s before object-level permission is even checked — not a 403 (previously untested
        # for consultations specifically; security audit, 2026-09-02).
        self.client.force_authenticate(self.doctor_b.user)
        response = self.client.get(reverse("consultation-detail", args=[self.consultation.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_validated_consultation_is_read_only(self):
        self.consultation.status = Consultation.Status.VALIDATED
        self.consultation.save()
        self.client.force_authenticate(self.doctor_a.user)
        response = self.client.patch(
            reverse("consultation-detail", args=[self.consultation.id]), {"diagnosis": "Changed"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_completing_requires_diagnosis_and_treatment_plan(self):
        self.client.force_authenticate(self.doctor_a.user)
        response = self.client.patch(
            reverse("consultation-detail", args=[self.consultation.id]),
            {"status": "completed"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ConsultationAuditTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00002")
        self.client.force_authenticate(self.doctor.user)

    def test_create_and_update_record_audit_log(self):
        payload = {
            "patient": self.patient.id,
            "doctor": self.doctor.id,
            "date": timezone.now().isoformat(),
        }
        create_response = self.client.post(reverse("consultation-list"), payload)
        consultation_id = create_response.data["id"]
        self.assertTrue(
            AuditLog.objects.filter(
                model_name="Consultation", object_id=str(consultation_id), action=AuditLog.Action.CREATE
            ).exists()
        )

        self.client.patch(
            reverse("consultation-detail", args=[consultation_id]), {"chief_complaint": "Headache"}
        )
        self.assertTrue(
            AuditLog.objects.filter(
                model_name="Consultation", object_id=str(consultation_id), action=AuditLog.Action.UPDATE
            ).exists()
        )


class ConsultationCrossClinicFKTests(APITestCase):
    """Regression coverage for the cross-tenant FK injection audit finding (confirmed by live
    exploit): a doctor must not be able to create a consultation for another clinic's patient, and
    a clinic_admin must not be able to assign another clinic's doctor to a consultation."""

    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.doctor_a = _create_doctor(self.clinic_a, "doc_a")
        self.doctor_b = _create_doctor(self.clinic_b, "doc_b")
        self.patient_a = _create_patient(self.clinic_a, "PAT-2026-00003")
        self.patient_b = _create_patient(self.clinic_b, "PAT-2026-00004")

    def test_doctor_cannot_create_consultation_for_other_clinics_patient(self):
        self.client.force_authenticate(self.doctor_a.user)
        payload = {
            "patient": self.patient_b.id,
            "doctor": self.doctor_a.id,
            "date": timezone.now().isoformat(),
            "chief_complaint": "leaked across tenants",
        }
        response = self.client.post(reverse("consultation-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Consultation.objects.filter(patient=self.patient_b).exists())

    def test_clinic_admin_cannot_assign_other_clinics_doctor(self):
        admin_a = create_user(clinic=self.clinic_a, role="clinic_admin")
        self.client.force_authenticate(admin_a)
        payload = {
            "patient": self.patient_a.id,
            "doctor": self.doctor_b.id,
            "date": timezone.now().isoformat(),
        }
        response = self.client.post(reverse("consultation-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_update_consultation_to_other_clinics_patient(self):
        self.client.force_authenticate(self.doctor_a.user)
        consultation = Consultation.objects.create(
            clinic=self.clinic_a, patient=self.patient_a, doctor=self.doctor_a, date=timezone.now()
        )
        response = self.client.patch(
            reverse("consultation-detail", args=[consultation.id]), {"patient": self.patient_b.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ConsultationSearchTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient_match = Patient.objects.create(
            clinic=self.clinic, patient_number="PAT-2026-00010", first_name="Aminata",
            last_name="Diallo", phone="0611111111", date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.patient_other = Patient.objects.create(
            clinic=self.clinic, patient_number="PAT-2026-00011", first_name="Boubacar",
            last_name="Kane", phone="0622222222", date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.consultation_match = Consultation.objects.create(
            clinic=self.clinic, patient=self.patient_match, doctor=self.doctor, date=timezone.now()
        )
        self.consultation_other = Consultation.objects.create(
            clinic=self.clinic, patient=self.patient_other, doctor=self.doctor, date=timezone.now()
        )
        self.client.force_authenticate(self.doctor.user)

    def test_search_by_patient_last_name_filters_results(self):
        response = self.client.get(reverse("consultation-list"), {"search": "Diallo"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.consultation_match.id, ids)
        self.assertNotIn(self.consultation_other.id, ids)

    def test_search_by_patient_number_filters_results(self):
        response = self.client.get(reverse("consultation-list"), {"search": "00010"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.consultation_match.id, ids)
        self.assertNotIn(self.consultation_other.id, ids)
