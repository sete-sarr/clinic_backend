"""Accès de l'infirmier en lecture (business/access-policy.md § INFIRMIER) : dossier médical,
prescriptions en cours et résultats de laboratoire validés des seuls patients hospitalisés — jamais
en écriture, jamais pour un patient non hospitalisé ou sorti."""

from datetime import date
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from common.testing import create_clinic, create_user
from consultations.models import Consultation
from departments.models import Department
from doctors.models import Doctor
from hospitalization.models import Bed, Room, RoomType
from hospitalization.services import create_admission, discharge
from laboratory.models import LabOrder, LabTest
from medical_records.models import MedicalRecord
from patients.models import Patient
from prescriptions.models import Prescription


class NurseReadAccessTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinic A")
        self.clinic.inpatient_nightly_rate = Decimal("1000")
        self.clinic.save(update_fields=["inpatient_nightly_rate"])
        doctor_user = create_user(clinic=self.clinic, role="doctor")
        self.doctor = Doctor.objects.create(user=doctor_user, clinic=self.clinic, professional_number="D1", specialty="G")
        self.nurse = create_user(clinic=self.clinic, role="nurse")
        department = Department.objects.create(clinic=self.clinic, name="Médecine", code="MED", department_type="medical")
        room_type = RoomType.objects.create(clinic=self.clinic, name="Double", nightly_rate=Decimal("1000"))
        room = Room.objects.create(clinic=self.clinic, department=department, number="1", room_type=room_type)
        self.bed = Bed.objects.create(clinic=self.clinic, room=room, label="A")
        self.inpatient = self._patient("PAT-1")
        self.outpatient = self._patient("PAT-2")
        self.stay = create_admission(
            clinic=self.clinic, doctor=self.doctor, patient=self.inpatient, department=department,
            reason="X", actor=doctor_user, bed=self.bed,
        )
        self.client.force_authenticate(self.nurse)

    def _patient(self, number):
        patient = Patient.objects.create(
            clinic=self.clinic, patient_number=number, first_name="P", last_name=number, phone="1",
            date_of_birth=date(1990, 1, 1), gender=Patient.Gender.OTHER,
        )
        MedicalRecord.objects.get_or_create(clinic=self.clinic, patient=patient, defaults={"allergies": "Pénicilline"})
        return patient

    def _prescription(self, patient, status):
        consultation = Consultation.objects.create(clinic=self.clinic, patient=patient, doctor=self.doctor, date=timezone.now())
        return Prescription.objects.create(clinic=self.clinic, consultation=consultation, patient=patient, doctor=self.doctor, status=status)

    def _lab_order(self, patient, status, number):
        test, _ = LabTest.objects.get_or_create(clinic=self.clinic, code="GLY", defaults={"name": "Glycémie", "price": 1})
        order = LabOrder.objects.create(clinic=self.clinic, number=number, patient=patient, doctor=self.doctor, status=status)
        order.items.create(test=test, test_code="GLY", test_name="Glycémie", price=1)
        return order

    def test_medical_record_of_inpatients_only_read_only(self):
        rows = self.client.get(reverse("medical-record-list")).data["results"]
        self.assertEqual([row["patient"] for row in rows], [self.inpatient.id])
        record = MedicalRecord.objects.get(patient=self.inpatient)
        self.assertEqual(self.client.get(reverse("medical-record-detail", args=[record.id])).status_code, 200)
        self.assertEqual(
            self.client.patch(reverse("medical-record-detail", args=[record.id]), {"allergies": "-"}, format="json").status_code, 403,
        )
        other = MedicalRecord.objects.get(patient=self.outpatient)
        self.assertEqual(self.client.get(reverse("medical-record-detail", args=[other.id])).status_code, 404)

    def test_access_ends_at_discharge(self):
        discharge(admission=self.stay, actor=self.doctor.user)
        self.assertEqual(self.client.get(reverse("medical-record-list")).data["count"], 0)

    def test_only_current_prescriptions_of_inpatients(self):
        validated = self._prescription(self.inpatient, Prescription.Status.VALIDATED)
        self._prescription(self.inpatient, Prescription.Status.DRAFT)
        self._prescription(self.outpatient, Prescription.Status.VALIDATED)
        rows = self.client.get(reverse("prescription-list")).data["results"]
        self.assertEqual([row["id"] for row in rows], [validated.id])
        response = self.client.patch(reverse("prescription-detail", args=[validated.id]), {"notes": "x"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_only_validated_lab_results_of_inpatients(self):
        validated = self._lab_order(self.inpatient, LabOrder.Status.VALIDATED, "LAB-1")
        self._lab_order(self.inpatient, LabOrder.Status.IN_PROGRESS, "LAB-2")
        self._lab_order(self.outpatient, LabOrder.Status.VALIDATED, "LAB-3")
        rows = self.client.get(reverse("lab-order-list")).data["results"]
        self.assertEqual([row["id"] for row in rows], [validated.id])
        self.assertNotIn("invoice", rows[0])
        self.assertEqual(self.client.get(reverse("lab-order-pdf", args=[validated.id])).status_code, 403)
