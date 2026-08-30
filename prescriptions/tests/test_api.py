from datetime import date

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from consultations.models import Consultation
from doctors.models import Doctor
from patients.models import Patient
from prescriptions.models import Prescription


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


class PrescriptionTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor_a = _create_doctor(self.clinic, "doc_a")
        self.doctor_b = _create_doctor(self.clinic, "doc_b")
        self.patient = _create_patient(self.clinic)
        self.consultation = Consultation.objects.create(
            clinic=self.clinic, patient=self.patient, doctor=self.doctor_a, date=timezone.now()
        )

    def test_prescription_requires_at_least_one_item(self):
        self.client.force_authenticate(self.doctor_a.user)
        payload = {
            "consultation": self.consultation.id,
            "patient": self.patient.id,
            "doctor": self.doctor_a.id,
            "items": [],
        }
        response = self.client.post(reverse("prescription-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_doctor_cannot_prescribe_under_another_doctors_name(self):
        # PrescriptionViewSet.perform_create always forces `doctor` to the requesting doctor's own
        # profile, ignoring whatever was submitted -- so doctor_b ends up owning the prescription
        # even though doctor_a.id was sent, rather than being rejected with 403.
        self.client.force_authenticate(self.doctor_b.user)
        payload = {
            "consultation": self.consultation.id,
            "patient": self.patient.id,
            "doctor": self.doctor_a.id,
            "items": [{"medication_name": "Amoxicillin", "dosage": "500mg", "frequency": "3x/day", "duration": "7d", "quantity": 21}],
        }
        response = self.client.post(reverse("prescription-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["doctor"], self.doctor_b.id)

    def test_validated_prescription_cannot_be_edited(self):
        prescription = Prescription.objects.create(
            clinic=self.clinic,
            consultation=self.consultation,
            patient=self.patient,
            doctor=self.doctor_a,
            status=Prescription.Status.VALIDATED,
        )
        self.client.force_authenticate(self.doctor_a.user)
        response = self.client.patch(reverse("prescription-detail", args=[prescription.id]), {"notes": "x"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_pdf_not_found_for_unrelated_doctor(self):
        # docs/known-issues.md #1 (resolved 2026-08-13): PrescriptionViewSet.get_queryset() scopes
        # a doctor to their own prescriptions before get_object() ever runs, so an unrelated
        # doctor's prescription is outside doctor_b's queryset entirely -> 404, not 403. This is
        # the intended, OWASP ASVS 4.0.3-aligned behavior (hide a resource's existence from a user
        # with no rights to it, rather than confirming it via 403) -- standardized across
        # appointments/consultations/prescriptions, not a bug.
        prescription = Prescription.objects.create(
            clinic=self.clinic, consultation=self.consultation, patient=self.patient, doctor=self.doctor_a
        )
        self.client.force_authenticate(self.doctor_b.user)
        response = self.client.get(reverse("prescription-pdf", args=[prescription.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_pdf_forbidden_for_staff_without_print_rights(self):
        # A secretary fails CanManagePrescriptions.has_permission() outright for the pdf action
        # (GET/SAFE_METHODS only allows doctor/clinic_admin/patient) -- 403 here confirms that gate
        # is still exercised and correct, distinct from the unrelated-doctor 404 case above (which
        # is scoped out at get_queryset()/get_object(), one layer deeper).
        prescription = Prescription.objects.create(
            clinic=self.clinic, consultation=self.consultation, patient=self.patient, doctor=self.doctor_a
        )
        secretary = create_user(clinic=self.clinic, role="secretary")
        self.client.force_authenticate(secretary)
        response = self.client.get(reverse("prescription-pdf", args=[prescription.id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PrescriptionCrossClinicFKTests(APITestCase):
    """Regression coverage for the cross-tenant FK injection audit finding (confirmed by live
    exploit): a doctor must not be able to prescribe to another clinic's patient."""

    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.doctor_a = _create_doctor(self.clinic_a, "doc_a2")
        self.patient_a = _create_patient(self.clinic_a, "PAT-2026-00005")
        self.patient_b = _create_patient(self.clinic_b, "PAT-2026-00006")
        self.consultation_a = Consultation.objects.create(
            clinic=self.clinic_a, patient=self.patient_a, doctor=self.doctor_a, date=timezone.now()
        )
        self.client.force_authenticate(self.doctor_a.user)

    def test_doctor_cannot_prescribe_to_other_clinics_patient(self):
        payload = {
            "consultation": self.consultation_a.id,
            "patient": self.patient_b.id,
            "doctor": self.doctor_a.id,
            "items": [{"medication_name": "Amoxicillin", "dosage": "500mg", "frequency": "3x/day", "duration": "7d", "quantity": 21}],
        }
        response = self.client.post(reverse("prescription-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Prescription.objects.filter(patient=self.patient_b).exists())

    def test_cannot_use_other_clinics_consultation(self):
        consultation_b_doctor = _create_doctor(self.clinic_b, "doc_b2")
        consultation_b = Consultation.objects.create(
            clinic=self.clinic_b, patient=self.patient_b, doctor=consultation_b_doctor, date=timezone.now()
        )
        payload = {
            "consultation": consultation_b.id,
            "patient": self.patient_a.id,
            "doctor": self.doctor_a.id,
            "items": [{"medication_name": "Amoxicillin", "dosage": "500mg", "frequency": "3x/day", "duration": "7d", "quantity": 21}],
        }
        response = self.client.post(reverse("prescription-list"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
