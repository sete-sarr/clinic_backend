"""Hospitalisation (docs/hospitalization.md) : machines à états du séjour et du lit, nuitées
(forfait / par type de chambre), facturation, constantes, visibilité par rôle, isolation."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import Invoice
from common.models import AuditLog
from common.testing import create_clinic, create_user
from communication.models import InAppNotification
from departments.models import Department
from doctors.models import Doctor
from hospitalization.models import Admission, Bed, BedTransfer, Room, RoomType
from hospitalization.services import night_lines
from patients.models import Patient


def _aware(*args):
    return timezone.make_aware(datetime(*args), timezone.get_current_timezone())


class HospitalizationTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinic A")
        self.clinic.inpatient_nightly_rate = Decimal("20000")
        self.clinic.save(update_fields=["inpatient_nightly_rate"])
        doctor_user = create_user(clinic=self.clinic, role="doctor", first_name="Awa", last_name="Diop")
        self.doctor = Doctor.objects.create(user=doctor_user, clinic=self.clinic, professional_number="D1", specialty="General")
        self.nurse = create_user(clinic=self.clinic, role="nurse")
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.accountant = create_user(clinic=self.clinic, role="accountant")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.department = Department.objects.create(
            clinic=self.clinic, name="Médecine", code="MED", department_type=Department.DepartmentType.MEDICAL,
        )
        self.single = RoomType.objects.create(clinic=self.clinic, name="Individuelle", nightly_rate=Decimal("30000"))
        self.double = RoomType.objects.create(clinic=self.clinic, name="Double", nightly_rate=Decimal("15000"))
        room_1 = Room.objects.create(clinic=self.clinic, department=self.department, number="101", room_type=self.single)
        room_2 = Room.objects.create(clinic=self.clinic, department=self.department, number="102", room_type=self.double)
        self.bed_1 = Bed.objects.create(clinic=self.clinic, room=room_1, label="A")
        self.bed_2 = Bed.objects.create(clinic=self.clinic, room=room_2, label="A")
        self.patient = Patient.objects.create(
            clinic=self.clinic, patient_number="PAT-2026-00001", first_name="Grace", last_name="Hopper",
            phone="770000000", date_of_birth=date(1990, 1, 1), gender=Patient.Gender.FEMALE,
        )

    def _as(self, user):
        self.client.force_authenticate(user)

    def _post(self, name, pk=None, data=None):
        url = reverse(name, args=[pk] if pk is not None else [])
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, data or {}, format="json")

    def _admit(self, bed=None):
        self._as(self.doctor.user)
        response = self._post("admission-list", data={
            "patient": self.patient.id, "department": self.department.id, "reason": "Pneumopathie",
            "bed": (bed or self.bed_1).id,
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response.data

    def _bed(self, bed):
        bed.refresh_from_db()
        return bed.status

    # ------------------------------------------------------------- admission

    def test_doctor_admits_occupies_bed_and_notifies_doctor_and_nurses(self):
        stay = self._admit()
        self.assertEqual(stay["status"], "admitted")
        self.assertTrue(stay["number"].startswith("HOS-"))
        self.assertEqual(self._bed(self.bed_1), Bed.Status.OCCUPIED)
        self.assertEqual(BedTransfer.objects.filter(admission_id=stay["id"], from_bed=None).count(), 1)
        recipients = set(InAppNotification.objects.values_list("recipient_id", flat=True))
        self.assertEqual(recipients, {self.doctor.user_id, self.nurse.id})
        self.assertNotIn("Pneumopathie", InAppNotification.objects.first().body)

    def test_only_doctor_admits(self):
        for user in (self.nurse, self.secretary, self.admin):
            self._as(user)
            response = self._post("admission-list", data={
                "patient": self.patient.id, "department": self.department.id, "reason": "X", "bed": self.bed_1.id,
            })
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admission_refused_without_configured_flat_rate(self):
        self.clinic.inpatient_nightly_rate = None
        self.clinic.save(update_fields=["inpatient_nightly_rate"])
        self._as(self.doctor.user)
        response = self._post("admission-list", data={
            "patient": self.patient.id, "department": self.department.id, "reason": "X", "bed": self.bed_1.id,
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Admission.objects.exists())
        self.assertEqual(self._bed(self.bed_1), Bed.Status.FREE)

    def test_bed_and_patient_cannot_be_taken_twice(self):
        self._admit()
        other = Patient.objects.create(
            clinic=self.clinic, patient_number="PAT-2026-00002", first_name="Ada", last_name="Lovelace",
            phone="770000001", date_of_birth=date(1990, 1, 1), gender=Patient.Gender.FEMALE,
        )
        busy_bed = self._post("admission-list", data={
            "patient": other.id, "department": self.department.id, "reason": "X", "bed": self.bed_1.id,
        })
        self.assertEqual(busy_bed.status_code, 400)
        same_patient = self._post("admission-list", data={
            "patient": self.patient.id, "department": self.department.id, "reason": "X", "bed": self.bed_2.id,
        })
        self.assertEqual(same_patient.status_code, 400)

    def test_planned_stay_admitted_later_or_cancelled(self):
        self._as(self.doctor.user)
        planned = self._post("admission-list", data={
            "patient": self.patient.id, "department": self.department.id, "reason": "Chirurgie programmée",
            "planned_for": "2026-10-20",
        }).data
        self.assertEqual(planned["status"], "planned")
        self.assertEqual(self._post("admission-cancel", planned["id"]).status_code, 200)
        self.assertEqual(self._post("admission-admit", planned["id"], {"bed": self.bed_1.id}).status_code, 400)

    # ------------------------------------------------------------- transfert et lits

    def test_nurse_transfers_bed_and_history_is_kept(self):
        stay = self._admit()
        self._as(self.nurse)
        response = self._post("admission-transfer", stay["id"], {"bed": self.bed_2.id, "reason": "Isolement"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._bed(self.bed_1), Bed.Status.CLEANING)
        self.assertEqual(self._bed(self.bed_2), Bed.Status.OCCUPIED)
        self.assertEqual(len(response.data["transfers"]), 2)
        self.assertEqual(self._post("bed-clean", self.bed_1.id).status_code, 200)
        self.assertEqual(self._bed(self.bed_1), Bed.Status.FREE)

    def test_bed_state_machine(self):
        self._admit()
        self._as(self.admin)
        self.assertEqual(self._post("bed-out-of-service", self.bed_1.id).status_code, 400)
        self.assertEqual(self._post("bed-archive", self.bed_1.id).status_code, 400)
        self.assertEqual(self._post("bed-out-of-service", self.bed_2.id).status_code, 200)
        self.assertEqual(self._post("bed-back-in-service", self.bed_2.id).status_code, 200)
        self._as(self.nurse)
        self.assertEqual(self._post("bed-out-of-service", self.bed_2.id).status_code, 403)

    def test_board_shows_location_only(self):
        self._admit()
        self._as(self.secretary)
        beds = self.client.get(reverse("bed-board")).data
        occupied = next(bed for bed in beds if bed["id"] == self.bed_1.id)
        self.assertEqual(set(occupied["current_stay"]), {"id", "number", "patient_display"})

    # ------------------------------------------------------------- nuitées et sortie

    def _stay_with_history(self, admitted_at, transfer_at=None):
        stay = Admission.objects.get(pk=self._admit()["id"])
        Admission.objects.filter(pk=stay.pk).update(admitted_at=admitted_at)
        BedTransfer.objects.filter(admission=stay).update(transferred_at=admitted_at)
        if transfer_at:
            BedTransfer.objects.create(admission=stay, from_bed=self.bed_1, to_bed=self.bed_2, transferred_at=transfer_at)
        stay.refresh_from_db()
        return stay

    def test_one_night_per_midnight_flat_rate(self):
        stay = self._stay_with_history(_aware(2026, 10, 1, 23, 30))
        nights, lines = night_lines(stay, _aware(2026, 10, 4, 8, 0))
        self.assertEqual(nights, 3)
        self.assertEqual([(line["quantity"], line["unit_price"]) for line in lines], [(3, Decimal("20000"))])
        self.assertEqual(night_lines(stay, _aware(2026, 10, 1, 23, 59))[0], 0)

    def test_per_room_type_counts_bed_occupied_at_midnight(self):
        self.clinic.inpatient_billing_mode = "per_room_type"
        self.clinic.save(update_fields=["inpatient_billing_mode"])
        stay = self._stay_with_history(_aware(2026, 10, 1, 10, 0), transfer_at=_aware(2026, 10, 3, 9, 0))
        nights, lines = night_lines(stay, _aware(2026, 10, 5, 11, 0))
        self.assertEqual(nights, 4)
        billed = {line["unit_price"]: line["quantity"] for line in lines}
        self.assertEqual(billed, {Decimal("30000"): 2, Decimal("15000"): 2})

    def test_discharge_bills_nights_frees_bed_and_notifies_front_desk(self):
        stay = self._stay_with_history(timezone.now() - timedelta(days=3))
        InAppNotification.objects.all().delete()
        self._as(self.doctor.user)
        response = self._post("admission-discharge", stay.pk, {"summary": "Évolution favorable."})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["nights"], 3)
        invoice = Invoice.objects.get(pk=response.data["invoice"])
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.subtotal, Decimal("60000.00"))
        self.assertEqual(self._bed(self.bed_1), Bed.Status.CLEANING)
        recipients = set(InAppNotification.objects.values_list("recipient_id", flat=True))
        self.assertEqual(recipients, {self.secretary.id, self.accountant.id})
        self.assertEqual(self._post("admission-discharge", stay.pk).status_code, 400)

    def test_same_day_discharge_creates_no_invoice(self):
        stay = self._admit()
        self._as(self.doctor.user)
        response = self._post("admission-discharge", stay["id"])
        self.assertEqual(response.data["nights"], 0)
        self.assertIsNone(response.data["invoice"])

    def test_only_doctor_discharges(self):
        stay = self._admit()
        for user in (self.nurse, self.admin):
            self._as(user)
            self.assertEqual(self._post("admission-discharge", stay["id"]).status_code, 403)

    # ------------------------------------------------------------- soins

    def test_vitals_and_notes(self):
        stay = self._admit()
        self._as(self.nurse)
        url = reverse("admission-vitals", args=[stay["id"]])
        self.assertEqual(self.client.post(url, {"temperature": "51"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        created = self.client.post(url, {"temperature": "38.5", "pulse": 96, "oxygen_saturation": 95}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(len(self.client.get(url).data), 1)
        notes = reverse("admission-notes", args=[stay["id"]])
        self.assertEqual(self.client.post(notes, {"note": "Patient calme."}, format="json").status_code, 201)
        self._as(self.secretary)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(notes).status_code, 403)

    def test_no_care_after_discharge(self):
        stay = self._admit()
        self._as(self.doctor.user)
        self._post("admission-discharge", stay["id"])
        self._as(self.nurse)
        response = self.client.post(reverse("admission-notes", args=[stay["id"]]), {"note": "X"}, format="json")
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------- visibilité

    def test_field_visibility_by_role(self):
        stay = self._stay_with_history(timezone.now() - timedelta(days=2))
        self._as(self.doctor.user)
        self._post("admission-discharge", stay.pk)
        url = reverse("admission-detail", args=[stay.pk])
        self._as(self.secretary)
        data = self.client.get(url).data
        self.assertNotIn("reason", data)
        self.assertNotIn("nights", data)
        self.assertNotIn("invoice", data)
        self.assertEqual(data["room_number"], "101")
        self._as(self.accountant)
        data = self.client.get(url).data
        self.assertNotIn("reason", data)
        self.assertEqual(data["nights"], 2)
        self.assertIn("invoice_number", data)
        self._as(self.nurse)
        data = self.client.get(url).data
        self.assertEqual(data["reason"], "Pneumopathie")
        self.assertNotIn("invoice", data)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.VIEW, model_name="Admission").exists())

    def test_nurse_never_sees_nightly_rates(self):
        self._as(self.nurse)
        room_type = self.client.get(reverse("room-type-detail", args=[self.single.id])).data
        self.assertNotIn("nightly_rate", room_type)
        clinic = self.client.get(reverse("clinic-detail", args=[self.clinic.id])).data
        self.assertNotIn("inpatient_nightly_rate", clinic)
        self._as(self.admin)
        self.assertIn("nightly_rate", self.client.get(reverse("room-type-detail", args=[self.single.id])).data)

    def test_other_clinic_isolated(self):
        stay = self._admit()
        other = create_clinic("Clinic B")
        self._as(create_user(clinic=other, role="nurse"))
        self.assertEqual(self.client.get(reverse("admission-list")).data["count"], 0)
        self.assertEqual(self.client.get(reverse("admission-detail", args=[stay["id"]])).status_code, 404)
        self.assertEqual(self.client.get(reverse("bed-board")).data, [])

    # ------------------------------------------------------------- paramétrage

    def test_admin_configures_rooms_and_billing_mode(self):
        self._as(self.admin)
        duplicate = self.client.post(reverse("room-list"), {
            "number": "101", "department": self.department.id, "room_type": self.single.id,
        }, format="json")
        self.assertEqual(duplicate.status_code, 400)
        invalid_rate = self.client.post(reverse("room-type-list"), {"name": "Soins intensifs", "nightly_rate": "0"}, format="json")
        self.assertEqual(invalid_rate.status_code, 400)
        url = reverse("clinic-detail", args=[self.clinic.id])
        self.assertEqual(self.client.patch(url, {"inpatient_billing_mode": "per_room_type"}, format="json").status_code, 200)
        self.assertEqual(
            self.client.patch(url, {"inpatient_billing_mode": "flat", "inpatient_nightly_rate": None}, format="json").status_code, 400,
        )
        self._as(self.nurse)
        self.assertEqual(self.client.post(reverse("room-type-list"), {"name": "X", "nightly_rate": "1"}, format="json").status_code, 403)
