"""Rendez-vous de département (rotation des médecins) : créé sans médecin par l'accueil, visible des
médecins du département, pris en charge par l'un d'eux ou attribué par l'accueil — journalisé."""

from datetime import date, time, timedelta

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from appointments.models import Appointment
from appointments.services import take_appointment
from common.models import AuditLog
from common.testing import create_clinic, create_user
from communication.models import InAppNotification, NotificationLog
from departments.models import Department
from departments.services import archive_department
from doctors.models import Doctor
from patients.models import Patient


def _department(clinic, name, **extra):
    return Department.objects.create(
        clinic=clinic, name=name, code=name[:3].upper(), department_type=Department.DepartmentType.MEDICAL, **extra
    )


def _doctor(clinic, name, department=None):
    user = create_user(clinic=clinic, role="doctor", first_name=name)
    return Doctor.objects.create(
        user=user, clinic=clinic, department=department, professional_number=f"D-{name}", specialty="General"
    )


def _patient(clinic, number):
    return Patient.objects.create(
        clinic=clinic, patient_number=number, first_name="P", last_name=number, phone="0600000000",
        date_of_birth=date(1990, 1, 1), gender=Patient.Gender.OTHER,
    )


class DepartmentAppointmentTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinique Rotation")
        self.cardio = _department(self.clinic, "Cardiologie")
        self.pedia = _department(self.clinic, "Pédiatrie")
        self.doctor_a = _doctor(self.clinic, "Alpha", self.cardio)
        self.doctor_b = _doctor(self.clinic, "Beta", self.cardio)
        self.doctor_p = _doctor(self.clinic, "Gamma", self.pedia)
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.patient = _patient(self.clinic, "PAT-R1")
        self.day = timezone.localdate() + timedelta(days=2)

    def _create(self, **payload):
        self.client.force_authenticate(self.secretary)
        data = {"patient": self.patient.id, "date": str(self.day), "time": "09:00", **payload}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("appointment-list"), data, format="json")

    def _pool_appointment(self):
        return Appointment.objects.create(
            clinic=self.clinic, patient=self.patient, department=self.cardio, date=self.day, time=time(9, 0)
        )

    def test_secretary_books_a_department_and_its_doctors_are_told(self):
        response = self._create(department=self.cardio.id)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(response.data["doctor"])
        self.assertEqual(response.data["department_display"], "Cardiologie")
        self.assertIn("Cardiologie", response.data["doctor_display"])
        notified = set(
            NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.APPOINTMENT_CREATED)
            .values_list("recipient_user_id", flat=True)
        )
        self.assertTrue({self.doctor_a.user_id, self.doctor_b.user_id} <= notified)
        self.assertNotIn(self.doctor_p.user_id, notified)

    def test_doctor_or_department_is_required_and_must_match(self):
        self.assertEqual(self._create().status_code, status.HTTP_400_BAD_REQUEST)
        response = self._create(doctor=self.doctor_p.id, department=self.cardio.id)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Un médecin seul : le rendez-vous porte son département.
        response = self._create(doctor=self.doctor_a.id)
        self.assertEqual(response.data["department"], self.cardio.id)

    def test_inactive_department_takes_no_new_appointment(self):
        self.cardio.status = Department.Status.INACTIVE
        self.cardio.save()
        self.assertEqual(self._create(department=self.cardio.id).status_code, status.HTTP_400_BAD_REQUEST)

    def test_department_doctors_see_the_pending_appointment_and_its_patient(self):
        appointment = self._pool_appointment()
        self.client.force_authenticate(self.doctor_a.user)
        ids = {item["id"] for item in self.client.get(reverse("appointment-list")).data["results"]}
        self.assertEqual(ids, {appointment.id})
        self.assertEqual(self.client.get(reverse("patient-detail", args=[self.patient.id])).status_code, 200)
        # Consultation seulement : ni modification ni annulation avant de l'avoir pris en charge.
        response = self.client.patch(reverse("appointment-detail", args=[appointment.id]), {"status": "cancelled"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.doctor_p.user)
        self.assertEqual(self.client.get(reverse("appointment-list")).data["count"], 0)
        self.assertEqual(self.client.get(reverse("patient-detail", args=[self.patient.id])).status_code, 404)

    def test_a_doctor_takes_it_and_it_leaves_the_others_lists(self):
        appointment = self._pool_appointment()
        self.client.force_authenticate(self.doctor_a.user)
        response = self.client.post(reverse("appointment-take", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["doctor"], self.doctor_a.id)
        log = AuditLog.objects.filter(action=AuditLog.Action.UPDATE, object_id=str(appointment.id)).get()
        self.assertEqual(log.metadata["taken_by_doctor"], self.doctor_a.id)

        self.client.force_authenticate(self.doctor_b.user)
        self.assertEqual(self.client.post(reverse("appointment-take", args=[appointment.id])).status_code, 404)
        with self.assertRaises(ValidationError):
            take_appointment(appointment=appointment, doctor=self.doctor_b)

    def test_doctor_of_another_department_cannot_take_it(self):
        appointment = self._pool_appointment()
        with self.assertRaises(ValidationError):
            take_appointment(appointment=appointment, doctor=self.doctor_p)

    def test_secretary_assigns_reassigns_and_returns_it_to_the_department(self):
        appointment = self._pool_appointment()
        url = reverse("appointment-assign", args=[appointment.id])
        self.client.force_authenticate(self.secretary)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {"doctor": self.doctor_b.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["doctor"], self.doctor_b.id)
        self.assertTrue(InAppNotification.objects.filter(recipient=self.doctor_b.user).exists())
        log = AuditLog.objects.get(action=AuditLog.Action.UPDATE, object_id=str(appointment.id))
        self.assertEqual((log.metadata["assigned_doctor"], log.metadata["previous_doctor"]), (self.doctor_b.id, None))

        response = self.client.post(url, {"doctor": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIsNone(response.data["doctor"])
        self.assertEqual(response.data["department"], self.cardio.id)

        self.client.force_authenticate(self.doctor_a.user)
        self.assertEqual(self.client.post(url, {"doctor": self.doctor_a.id}, format="json").status_code, 403)

    def test_assigning_a_doctor_from_another_clinic_is_rejected(self):
        appointment = self._pool_appointment()
        outsider = _doctor(create_clinic("Ailleurs"), "Delta")
        self.client.force_authenticate(self.secretary)
        response = self.client.post(
            reverse("appointment-assign", args=[appointment.id]), {"doctor": outsider.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_check_in_of_a_pending_appointment_alerts_the_department(self):
        appointment = self._pool_appointment()
        appointment.date = timezone.localdate()
        appointment.save()
        self.client.force_authenticate(self.secretary)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("appointment-check-in", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        recipients = set(InAppNotification.objects.values_list("recipient_id", flat=True))
        self.assertEqual(recipients, {self.doctor_a.user_id, self.doctor_b.user_id})

    def test_department_with_pending_appointments_cannot_be_archived(self):
        self._pool_appointment()
        for doctor in (self.doctor_a, self.doctor_b):
            doctor.is_active = False
            doctor.save()
        with self.assertRaises(ValidationError):
            archive_department(department=self.cardio, actor=self.secretary)


class AppointmentPeriodTests(APITestCase):
    """Liste des rendez-vous : « À venir » (le plus proche en premier) et « Passés » (le plus récent en premier)."""

    def setUp(self):
        self.clinic = create_clinic("Clinique Période")
        doctor = _doctor(self.clinic, "Epsilon")
        patient = _patient(self.clinic, "PAT-P1")
        today = timezone.localdate()
        self.ids = {}
        for key, offset in {"far": 20, "soon": 1, "today": 0, "yesterday": -1, "old": -30}.items():
            self.ids[key] = Appointment.objects.create(
                clinic=self.clinic, patient=patient, doctor=doctor, date=today + timedelta(days=offset), time=time(9, 0)
            ).id
        self.client.force_authenticate(create_user(clinic=self.clinic, role="secretary"))

    def _ids(self, period):
        response = self.client.get(reverse("appointment-list"), {"period": period})
        return [item["id"] for item in response.data["results"]]

    def test_upcoming_nearest_first_and_past_latest_first(self):
        self.assertEqual(self._ids("upcoming"), [self.ids["today"], self.ids["soon"], self.ids["far"]])
        self.assertEqual(self._ids("past"), [self.ids["yesterday"], self.ids["old"]])
