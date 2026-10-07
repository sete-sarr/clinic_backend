"""Centre de notifications in-app : création par communication.services.notify_in_app (langue du
destinataire), API scopée au destinataire et à sa clinique, purge des notifications archivées."""

from datetime import date, timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from rest_framework import status
from rest_framework.test import APITestCase

from appointments.models import Appointment
from common.testing import create_clinic, create_user
from communication.models import InAppNotification
from communication.services import notify_in_app
from communication.tasks import purge_archived_in_app_notifications
from doctors.models import Doctor
from patients.models import Patient


def _notify(clinic, users, title="Alerte"):
    notify_in_app(
        clinic=clinic, recipients=users, category=InAppNotification.Category.SYSTEM,
        priority=InAppNotification.Priority.HIGH, compose=lambda: (title, "Détail"), link="/dashboard",
    )


class NotifyInAppServiceTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinic A")
        self.user = create_user(clinic=self.clinic, role="secretary")

    def test_created_after_commit_once_per_active_recipient(self):
        inactive = create_user(clinic=self.clinic, role="secretary", is_active=False)
        with self.captureOnCommitCallbacks(execute=True):
            _notify(self.clinic, [self.user, self.user, inactive, None])
        self.assertEqual(InAppNotification.objects.count(), 1)
        self.assertEqual(InAppNotification.objects.get().recipient, self.user)

    def test_composed_in_recipient_language(self):
        self.user.language = "en"
        self.user.save(update_fields=["language"])
        with self.captureOnCommitCallbacks(execute=True):
            notify_in_app(
                clinic=self.clinic, recipients=[self.user], category=InAppNotification.Category.SYSTEM,
                priority=InAppNotification.Priority.LOW, compose=lambda: (get_language(), ""),
            )
        self.assertEqual(InAppNotification.objects.get().title, "en")


class InAppNotificationApiTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinic A")
        self.other_clinic = create_clinic("Clinic B")
        self.user = create_user(clinic=self.clinic, role="secretary")
        self.colleague = create_user(clinic=self.clinic, role="secretary")
        self.foreign = create_user(clinic=self.other_clinic, role="secretary")
        with self.captureOnCommitCallbacks(execute=True):
            _notify(self.clinic, [self.user], "Mine 1")
            _notify(self.clinic, [self.user], "Mine 2")
            _notify(self.clinic, [self.colleague], "Colleague")
            _notify(self.other_clinic, [self.foreign], "Foreign")
        self.client.force_authenticate(self.user)

    def _titles(self, **params):
        response = self.client.get(reverse("notification-list"), params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return sorted(row["title"] for row in response.data["results"])

    def test_list_only_own_notifications(self):
        self.assertEqual(self._titles(), ["Mine 1", "Mine 2"])

    def test_cannot_read_someone_elses_notification(self):
        other = InAppNotification.objects.get(title="Colleague")
        response = self.client.post(reverse("notification-read", args=[other.id]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        other.refresh_from_db()
        self.assertIsNone(other.read_at)

    def test_unread_count_read_and_read_all(self):
        url = reverse("notification-unread-count")
        self.assertEqual(self.client.get(url).data["count"], 2)
        first = InAppNotification.objects.get(title="Mine 1")
        response = self.client.post(reverse("notification-read", args=[first.id]))
        self.assertTrue(response.data["is_read"])
        self.assertEqual(self.client.get(url).data["count"], 1)
        self.assertEqual(self.client.post(reverse("notification-read-all")).data["updated"], 1)
        self.assertEqual(self.client.get(url).data["count"], 0)
        self.assertIsNone(InAppNotification.objects.get(title="Colleague").read_at)

    def test_archive_hides_from_active_list_and_marks_read(self):
        mine = InAppNotification.objects.get(title="Mine 1")
        response = self.client.post(reverse("notification-archive", args=[mine.id]))
        self.assertTrue(response.data["is_read"])
        self.assertEqual(self._titles(archived="false"), ["Mine 2"])
        self.assertEqual(self._titles(archived="true"), ["Mine 1"])

    def test_unread_filter_and_search(self):
        InAppNotification.objects.filter(title="Mine 1").update(read_at=timezone.now())
        self.assertEqual(self._titles(unread="true"), ["Mine 2"])
        self.assertEqual(self._titles(search="Mine 1"), ["Mine 1"])

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        response = self.client.get(reverse("notification-list"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PurgeArchivedTests(APITestCase):
    def test_purges_only_old_archived(self):
        clinic = create_clinic("Clinic A")
        user = create_user(clinic=clinic, role="secretary")
        with self.captureOnCommitCallbacks(execute=True):
            _notify(clinic, [user], "Old")
            _notify(clinic, [user], "Recent")
            _notify(clinic, [user], "Active")
        now = timezone.now()
        InAppNotification.objects.filter(title="Old").update(archived_at=now - timedelta(days=91))
        InAppNotification.objects.filter(title="Recent").update(archived_at=now - timedelta(days=10))
        purge_archived_in_app_notifications()
        self.assertEqual(set(InAppNotification.objects.values_list("title", flat=True)), {"Recent", "Active"})


class CheckInNotifiesDoctorTests(APITestCase):
    def test_doctor_notified_on_check_in(self):
        clinic = create_clinic("Clinic A")
        doctor_user = create_user(clinic=clinic, role="doctor", first_name="Awa", last_name="Diop")
        doctor = Doctor.objects.create(user=doctor_user, clinic=clinic, professional_number="DOC-1", specialty="General")
        patient = Patient.objects.create(
            clinic=clinic, patient_number="PAT-2026-00001", first_name="Grace", last_name="Hopper",
            phone="0600000000", date_of_birth=date(1990, 1, 1), gender=Patient.Gender.FEMALE,
        )
        appointment = Appointment.objects.create(
            clinic=clinic, doctor=doctor, patient=patient, date=timezone.localdate(), time=timezone.now().time(),
        )
        self.client.force_authenticate(create_user(clinic=clinic, role="secretary"))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("appointment-check-in", args=[appointment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification = InAppNotification.objects.get(recipient=doctor_user)
        self.assertEqual(notification.category, InAppNotification.Category.APPOINTMENT)
        self.assertIn("Grace Hopper", notification.title)
        self.assertIn(response.data["ticket_number"], notification.body)
        self.assertEqual(notification.link, "/appointments?checkedIn=true")
