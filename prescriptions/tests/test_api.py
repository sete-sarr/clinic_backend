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
        # PrescriptionViewSet.perform_create force toujours `doctor` au profil du médecin à
        # l'origine de la requête, en ignorant ce qui a été soumis -- donc doctor_b se retrouve
        # propriétaire de la prescription même si doctor_a.id a été envoyé, plutôt que d'être
        # rejeté avec un 403.
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
        # docs/known-issues.md #1 (résolu le 2026-08-13) : PrescriptionViewSet.get_queryset()
        # restreint un médecin à ses propres prescriptions avant même que get_object() ne s'exécute,
        # donc la prescription d'un médecin non lié est entièrement hors du queryset de doctor_b
        # -> 404, pas 403. C'est le comportement voulu, aligné sur OWASP ASVS 4.0.3 (cacher
        # l'existence d'une ressource à un utilisateur qui n'a aucun droit dessus, plutôt que de la
        # confirmer via un 403) -- standardisé sur appointments/consultations/prescriptions, ce
        # n'est pas un bug.
        prescription = Prescription.objects.create(
            clinic=self.clinic, consultation=self.consultation, patient=self.patient, doctor=self.doctor_a
        )
        self.client.force_authenticate(self.doctor_b.user)
        response = self.client.get(reverse("prescription-pdf", args=[prescription.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_pdf_forbidden_for_staff_without_print_rights(self):
        # Un(e) secrétaire échoue d'emblée à CanManagePrescriptions.has_permission() pour l'action
        # pdf (GET/SAFE_METHODS n'autorise que doctor/clinic_admin/patient) -- le 403 ici confirme
        # que cette barrière est toujours exercée et correcte, ce qui est distinct du cas 404 du
        # médecin non lié ci-dessus (qui est filtré au niveau de get_queryset()/get_object(), une
        # couche plus bas).
        prescription = Prescription.objects.create(
            clinic=self.clinic, consultation=self.consultation, patient=self.patient, doctor=self.doctor_a
        )
        secretary = create_user(clinic=self.clinic, role="secretary")
        self.client.force_authenticate(secretary)
        response = self.client.get(reverse("prescription-pdf", args=[prescription.id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PrescriptionCrossClinicFKTests(APITestCase):
    """Couverture de non-régression pour le constat d'audit sur l'injection de FK cross-tenant
    (confirmé par un exploit réel) : un médecin ne doit pas pouvoir prescrire à un patient d'une
    autre clinique."""

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


class PrescriptionSearchTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient_match = Patient.objects.create(
            clinic=self.clinic, patient_number="PAT-2026-00020", first_name="Aminata",
            last_name="Diallo", phone="0611111111", date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        self.patient_other = Patient.objects.create(
            clinic=self.clinic, patient_number="PAT-2026-00021", first_name="Boubacar",
            last_name="Kane", phone="0622222222", date_of_birth=date(1990, 1, 1),
            gender=Patient.Gender.OTHER,
        )
        consultation_match = Consultation.objects.create(
            clinic=self.clinic, patient=self.patient_match, doctor=self.doctor, date=timezone.now()
        )
        consultation_other = Consultation.objects.create(
            clinic=self.clinic, patient=self.patient_other, doctor=self.doctor, date=timezone.now()
        )
        item = {"medication_name": "Amoxicillin", "dosage": "500mg", "frequency": "3x/day", "duration": "7d", "quantity": 21}
        self.prescription_match = Prescription.objects.create(
            clinic=self.clinic, consultation=consultation_match, patient=self.patient_match, doctor=self.doctor
        )
        self.prescription_match.items.create(**item)
        self.prescription_other = Prescription.objects.create(
            clinic=self.clinic, consultation=consultation_other, patient=self.patient_other, doctor=self.doctor
        )
        self.prescription_other.items.create(**item)
        self.client.force_authenticate(self.doctor.user)

    def test_search_by_patient_last_name_filters_results(self):
        response = self.client.get(reverse("prescription-list"), {"search": "Diallo"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.prescription_match.id, ids)
        self.assertNotIn(self.prescription_other.id, ids)

    def test_search_by_patient_number_filters_results(self):
        response = self.client.get(reverse("prescription-list"), {"search": "00020"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.prescription_match.id, ids)
        self.assertNotIn(self.prescription_other.id, ids)
