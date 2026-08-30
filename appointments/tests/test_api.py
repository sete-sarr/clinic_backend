from datetime import date, timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from appointments.models import Appointment
from common.models import AuditLog
from common.testing import create_clinic, create_user
from doctors.models import Doctor
from patients.models import Patient


def _create_doctor(clinic, username=None):
    user = create_user(clinic=clinic, role="doctor", username=username)
    return Doctor.objects.create(
        user=user, clinic=clinic, professional_number=f"DOC-{user.id}", specialty="General"
    )


def _create_patient(clinic, number):
    return Patient.objects.create(
        clinic=clinic,
        patient_number=number,
        first_name="Test",
        last_name="Patient",
        phone="0600000000",
        date_of_birth=date(1990, 1, 1),
        gender=Patient.Gender.OTHER,
    )


def _create_patient_with_user(clinic, number, username=None):
    user = create_user(clinic=clinic, role="patient", username=username, email=f"{number.lower()}@example.com")
    patient = _create_patient(clinic, number)
    patient.user = user
    patient.email = user.email
    patient.save(update_fields=["user", "email"])
    return patient


class AppointmentTenantIsolationTests(APITestCase):
    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.secretary_a = create_user(clinic=self.clinic_a, role="secretary")
        self.secretary_b = create_user(clinic=self.clinic_b, role="secretary")
        self.doctor_a = _create_doctor(self.clinic_a)
        self.patient_a = _create_patient(self.clinic_a, "PAT-2026-00001")
        self.appointment_a = Appointment.objects.create(
            clinic=self.clinic_a,
            patient=self.patient_a,
            doctor=self.doctor_a,
            date=date.today() + timedelta(days=1),
            time="09:00:00",
        )

    def test_clinic_b_cannot_see_clinic_a_appointment(self):
        self.client.force_authenticate(self.secretary_b)
        response = self.client.get(reverse("appointment-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(self.appointment_a.id, ids)


class AppointmentOwnerIsolationTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor_a = _create_doctor(self.clinic, username="doc_a")
        self.doctor_b = _create_doctor(self.clinic, username="doc_b")
        self.patient = _create_patient(self.clinic, "PAT-2026-00002")
        self.appointment_for_a = Appointment.objects.create(
            clinic=self.clinic,
            patient=self.patient,
            doctor=self.doctor_a,
            date=date.today() + timedelta(days=1),
            time="10:00:00",
        )

    def test_doctor_b_cannot_see_doctor_a_appointment(self):
        self.client.force_authenticate(self.doctor_b.user)
        response = self.client.get(reverse("appointment-list"))
        ids = [item["id"] for item in response.data["results"]]
        self.assertNotIn(self.appointment_for_a.id, ids)

    def test_doctor_cannot_create_appointment_for_another_doctor(self):
        self.client.force_authenticate(self.doctor_b.user)
        payload = {
            "patient": self.patient.id,
            "doctor": self.doctor_a.id,
            "date": (date.today() + timedelta(days=2)).isoformat(),
            "time": "11:00:00",
        }
        response = self.client.post(reverse("appointment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AppointmentBusinessRuleTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00003")
        self.client.force_authenticate(self.doctor.user)

    def test_cannot_create_appointment_in_the_past(self):
        payload = {
            "patient": self.patient.id,
            "doctor": self.doctor.id,
            "date": (date.today() - timedelta(days=1)).isoformat(),
            "time": "09:00:00",
        }
        response = self.client.post(reverse("appointment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_double_book_same_doctor_slot(self):
        slot_date = (date.today() + timedelta(days=3)).isoformat()
        payload = {
            "patient": self.patient.id,
            "doctor": self.doctor.id,
            "date": slot_date,
            "time": "09:00:00",
        }
        first = self.client.post(reverse("appointment-list"), payload)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        other_patient = _create_patient(self.clinic, "PAT-2026-00004")
        payload["patient"] = other_patient.id
        second = self.client.post(reverse("appointment-list"), payload)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)


class AppointmentAuditTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00005")
        self.client.force_authenticate(self.doctor.user)

    def test_create_and_update_record_audit_log(self):
        payload = {
            "patient": self.patient.id,
            "doctor": self.doctor.id,
            "date": (date.today() + timedelta(days=5)).isoformat(),
            "time": "09:00:00",
        }
        create_response = self.client.post(reverse("appointment-list"), payload)
        appointment_id = create_response.data["id"]
        self.assertTrue(
            AuditLog.objects.filter(
                model_name="Appointment", object_id=str(appointment_id), action=AuditLog.Action.CREATE
            ).exists()
        )

        self.client.patch(reverse("appointment-detail", args=[appointment_id]), {"reason": "Follow-up"})
        self.assertTrue(
            AuditLog.objects.filter(
                model_name="Appointment", object_id=str(appointment_id), action=AuditLog.Action.UPDATE
            ).exists()
        )


class AppointmentCsvExportTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00006")
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        Appointment.objects.create(
            clinic=self.clinic,
            patient=self.patient,
            doctor=self.doctor,
            date=date.today() + timedelta(days=1),
            time="09:00:00",
        )

    def test_csv_export_returns_csv_with_header(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.get(reverse("appointment-export-csv"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode()
        self.assertIn("patient_display", content.splitlines()[0])

    def test_csv_export_records_audit_log(self):
        self.client.force_authenticate(self.secretary)
        self.client.get(reverse("appointment-export-csv"))
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.EXPORT, model_name="appointment").exists())


class AppointmentPatientCreateTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient_with_user(self.clinic, "PAT-2026-00007")
        self.other_patient = _create_patient(self.clinic, "PAT-2026-00008")
        self.client.force_authenticate(self.patient.user)

    def test_patient_can_create_own_appointment(self):
        payload = {
            "doctor": self.doctor.id,
            "date": (date.today() + timedelta(days=2)).isoformat(),
            "time": "09:00:00",
            "reason": "Check-up",
        }
        response = self.client.post(reverse("appointment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        appointment = Appointment.objects.get(id=response.data["id"])
        self.assertEqual(appointment.patient, self.patient)

    def test_patient_cannot_create_appointment_for_another_patient(self):
        payload = {
            "patient": self.other_patient.id,
            "doctor": self.doctor.id,
            "date": (date.today() + timedelta(days=2)).isoformat(),
            "time": "09:30:00",
            "reason": "Check-up",
        }
        response = self.client.post(reverse("appointment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        appointment = Appointment.objects.get(id=response.data["id"])
        self.assertEqual(appointment.patient, self.patient)  # server-resolved, client value ignored

    def test_patient_without_profile_cannot_create(self):
        orphan_user = create_user(clinic=self.clinic, role="patient")
        self.client.force_authenticate(orphan_user)
        payload = {
            "doctor": self.doctor.id,
            "date": (date.today() + timedelta(days=2)).isoformat(),
            "time": "10:00:00",
        }
        response = self.client.post(reverse("appointment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AppointmentPatientCancelTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient_with_user(self.clinic, "PAT-2026-00009")
        self.other_patient = _create_patient_with_user(self.clinic, "PAT-2026-00010")

    def _appointment_at(self, patient, dt, appointment_status=Appointment.Status.PENDING):
        return Appointment.objects.create(
            clinic=self.clinic,
            patient=patient,
            doctor=self.doctor,
            date=dt.date(),
            time=dt.time(),
            status=appointment_status,
        )

    def test_patient_can_cancel_own_pending_appointment_more_than_24h_out(self):
        appointment = self._appointment_at(self.patient, timezone.now() + timedelta(hours=48))
        self.client.force_authenticate(self.patient.user)
        response = self.client.post(reverse("appointment-cancel", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.CANCELLED)

    def test_patient_can_cancel_own_confirmed_appointment_more_than_24h_out(self):
        appointment = self._appointment_at(
            self.patient, timezone.now() + timedelta(hours=48), appointment_status=Appointment.Status.CONFIRMED
        )
        self.client.force_authenticate(self.patient.user)
        response = self.client.post(reverse("appointment-cancel", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_patient_cannot_cancel_less_than_24h_out(self):
        appointment = self._appointment_at(self.patient, timezone.now() + timedelta(hours=1))
        self.client.force_authenticate(self.patient.user)
        response = self.client.post(reverse("appointment-cancel", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.PENDING)

    def test_patient_cannot_cancel_someone_elses_appointment(self):
        # docs/known-issues.md #1: the appointment is outside this patient's queryset entirely,
        # so it 404s before object-level permission is even checked — not a 403.
        appointment = self._appointment_at(self.other_patient, timezone.now() + timedelta(hours=48))
        self.client.force_authenticate(self.patient.user)
        response = self.client.post(reverse("appointment-cancel", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_patient_cannot_cancel_already_cancelled_appointment(self):
        appointment = self._appointment_at(
            self.patient, timezone.now() + timedelta(hours=48), appointment_status=Appointment.Status.CANCELLED
        )
        self.client.force_authenticate(self.patient.user)
        response = self.client.post(reverse("appointment-cancel", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patient_cannot_cancel_completed_appointment(self):
        appointment = self._appointment_at(
            self.patient, timezone.now() + timedelta(hours=48), appointment_status=Appointment.Status.COMPLETED
        )
        self.client.force_authenticate(self.patient.user)
        response = self.client.post(reverse("appointment-cancel", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patient_cannot_cancel_no_show_appointment(self):
        appointment = self._appointment_at(
            self.patient, timezone.now() + timedelta(hours=48), appointment_status=Appointment.Status.NO_SHOW
        )
        self.client.force_authenticate(self.patient.user)
        response = self.client.post(reverse("appointment-cancel", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_can_still_cancel_via_generic_patch(self):
        appointment = self._appointment_at(self.patient, timezone.now() + timedelta(hours=1))
        secretary = create_user(clinic=self.clinic, role="secretary")
        self.client.force_authenticate(secretary)
        response = self.client.patch(
            reverse("appointment-detail", args=[appointment.id]), {"status": Appointment.Status.CANCELLED}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.CANCELLED)


class AppointmentCheckInTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00012")
        self.secretary = create_user(clinic=self.clinic, role="secretary")

    def _appointment_today(self, appointment_status=Appointment.Status.PENDING):
        now = timezone.now()
        return Appointment.objects.create(
            clinic=self.clinic, doctor=self.doctor, patient=self.patient,
            date=now.date(), time=now.time(), status=appointment_status,
        )

    def test_secretary_can_check_in(self):
        appointment = self._appointment_today()
        self.client.force_authenticate(self.secretary)
        response = self.client.post(reverse("appointment-check-in", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["checked_in_at"])
        self.assertTrue(response.data["ticket_number"].startswith("CHK-"))

    def test_clinic_admin_can_check_in(self):
        appointment = self._appointment_today()
        clinic_admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.client.force_authenticate(clinic_admin)
        response = self.client.post(reverse("appointment-check-in", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_own_doctor_can_check_in(self):
        appointment = self._appointment_today()
        self.client.force_authenticate(self.doctor.user)
        response = self.client.post(reverse("appointment-check-in", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_patient_cannot_check_in(self):
        appointment = self._appointment_today()
        patient_user = create_user(clinic=self.clinic, role="patient")
        self.client.force_authenticate(patient_user)
        response = self.client.post(reverse("appointment-check-in", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_today_appointment_returns_400(self):
        appointment = Appointment.objects.create(
            clinic=self.clinic, doctor=self.doctor, patient=self.patient,
            date=(timezone.now() + timedelta(days=1)).date(), time="09:00:00",
        )
        self.client.force_authenticate(self.secretary)
        response = self.client.post(reverse("appointment-check-in", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_check_in_records_audit_log(self):
        appointment = self._appointment_today()
        self.client.force_authenticate(self.secretary)
        self.client.post(reverse("appointment-check-in", args=[appointment.id]))
        self.assertTrue(
            AuditLog.objects.filter(model_name="Appointment", object_id=str(appointment.id)).exists()
        )


class AppointmentTicketPdfTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.patient = _create_patient(self.clinic, "PAT-2026-00013")
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        now = timezone.now()
        self.appointment = Appointment.objects.create(
            clinic=self.clinic, doctor=self.doctor, patient=self.patient,
            date=now.date(), time=now.time(),
        )

    def test_not_checked_in_yet_returns_400(self):
        self.client.force_authenticate(self.secretary)
        response = self.client.get(reverse("appointment-ticket-pdf", args=[self.appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_returns_pdf_after_check_in(self):
        self.client.force_authenticate(self.secretary)
        self.client.post(reverse("appointment-check-in", args=[self.appointment.id]))
        response = self.client.get(reverse("appointment-ticket-pdf", args=[self.appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_unrelated_doctor_gets_404(self):
        # docs/known-issues.md #1 (resolved): get_queryset() scopes a doctor to their own
        # appointments before get_object() runs, so an unrelated doctor's appointment is outside
        # the queryset entirely -> 404, not 403 (standardized pattern, not a bug).
        self.client.force_authenticate(self.secretary)
        self.client.post(reverse("appointment-check-in", args=[self.appointment.id]))
        other_doctor = _create_doctor(self.clinic, username="other_doc")
        self.client.force_authenticate(other_doctor.user)
        response = self.client.get(reverse("appointment-ticket-pdf", args=[self.appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AppointmentFilterSetTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.doctor = _create_doctor(self.clinic)
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.patient_a = _create_patient(self.clinic, "PAT-2026-00014")
        self.patient_b = _create_patient(self.clinic, "PAT-2026-00015")
        now = timezone.now()
        self.appointment_a = Appointment.objects.create(
            clinic=self.clinic, doctor=self.doctor, patient=self.patient_a, date=now.date(), time="09:00:00",
        )
        self.appointment_b = Appointment.objects.create(
            clinic=self.clinic, doctor=self.doctor, patient=self.patient_b, date=now.date(), time="10:00:00",
        )
        self.client.force_authenticate(self.secretary)

    def test_filter_by_patient_number_substring(self):
        response = self.client.get(reverse("appointment-list"), {"patient_number": "00014"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(ids, [self.appointment_a.id])

    def test_filter_checked_in_true(self):
        self.client.post(reverse("appointment-check-in", args=[self.appointment_a.id]))
        response = self.client.get(reverse("appointment-list"), {"checked_in": "true"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(ids, [self.appointment_a.id])

    def test_filter_checked_in_false(self):
        self.client.post(reverse("appointment-check-in", args=[self.appointment_a.id]))
        response = self.client.get(reverse("appointment-list"), {"checked_in": "false"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(ids, [self.appointment_b.id])

    def test_existing_status_filter_still_works(self):
        self.appointment_a.status = Appointment.Status.CONFIRMED
        self.appointment_a.save(update_fields=["status"])
        response = self.client.get(reverse("appointment-list"), {"status": "confirmed"})
        ids = [item["id"] for item in response.data["results"]]
        self.assertEqual(ids, [self.appointment_a.id])

    def test_existing_date_filter_still_works(self):
        yesterday = (timezone.now() - timedelta(days=1)).date().isoformat()
        response = self.client.get(reverse("appointment-list"), {"date": yesterday})
        self.assertEqual(response.data["results"], [])


class AppointmentCrossClinicFKTests(APITestCase):
    """Regression coverage for the cross-tenant FK injection audit finding: a staff user must not
    be able to create/update an appointment referencing another clinic's patient or doctor."""

    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.secretary_a = create_user(clinic=self.clinic_a, role="secretary")
        self.doctor_a = _create_doctor(self.clinic_a)
        self.patient_a = _create_patient(self.clinic_a, "PAT-2026-00016")
        self.doctor_b = _create_doctor(self.clinic_b)
        self.patient_b = _create_patient(self.clinic_b, "PAT-2026-00017")
        self.client.force_authenticate(self.secretary_a)

    def test_cannot_create_appointment_with_other_clinics_patient(self):
        payload = {
            "patient": self.patient_b.id,
            "doctor": self.doctor_a.id,
            "date": (date.today() + timedelta(days=2)).isoformat(),
            "time": "09:00:00",
        }
        response = self.client.post(reverse("appointment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_create_appointment_with_other_clinics_doctor(self):
        payload = {
            "patient": self.patient_a.id,
            "doctor": self.doctor_b.id,
            "date": (date.today() + timedelta(days=2)).isoformat(),
            "time": "09:00:00",
        }
        response = self.client.post(reverse("appointment-list"), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_update_appointment_to_other_clinics_patient(self):
        appointment = Appointment.objects.create(
            clinic=self.clinic_a, doctor=self.doctor_a, patient=self.patient_a,
            date=date.today() + timedelta(days=2), time="09:00:00",
        )
        response = self.client.patch(
            reverse("appointment-detail", args=[appointment.id]), {"patient": self.patient_b.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        appointment.refresh_from_db()
        self.assertEqual(appointment.patient, self.patient_a)
