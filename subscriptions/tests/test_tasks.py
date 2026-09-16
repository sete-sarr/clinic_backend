from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from clinics.models import Clinic
from common.testing import create_clinic, create_user
from communication.models import NotificationLog
from subscriptions.tasks import send_subscription_expiring_notifications


def _active_clinic_expiring_in(days):
    clinic = create_clinic()
    clinic.subscription_status = Clinic.SubscriptionStatus.ACTIVE
    clinic.current_period_end = timezone.now() + timedelta(days=days)
    clinic.save()
    return clinic


class SendSubscriptionExpiringNotificationsTests(TestCase):
    def test_sends_at_each_threshold(self):
        for days in (30, 15, 7, 1):
            clinic = _active_clinic_expiring_in(days)
            create_user(clinic=clinic, role="clinic_admin", email=f"admin{days}@example.com")

        send_subscription_expiring_notifications()

        logs = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.SUBSCRIPTION_EXPIRING)
        self.assertEqual(logs.count(), 4)

    def test_idempotent_same_day_rerun(self):
        clinic = _active_clinic_expiring_in(30)
        create_user(clinic=clinic, role="clinic_admin", email="admin@example.com")

        send_subscription_expiring_notifications()
        send_subscription_expiring_notifications()

        logs = NotificationLog.objects.filter(notification_type=NotificationLog.NotificationType.SUBSCRIPTION_EXPIRING)
        self.assertEqual(logs.count(), 1)

    def test_skips_trial_and_suspended_and_cancelled_clinics(self):
        for sub_status in (Clinic.SubscriptionStatus.TRIAL, Clinic.SubscriptionStatus.SUSPENDED, Clinic.SubscriptionStatus.CANCELLED):
            clinic = create_clinic()
            clinic.subscription_status = sub_status
            clinic.current_period_end = timezone.now() + timedelta(days=30)
            clinic.save()
            create_user(clinic=clinic, role="clinic_admin", email=f"{sub_status}@example.com")

        send_subscription_expiring_notifications()

        self.assertEqual(
            NotificationLog.objects.filter(
                notification_type=NotificationLog.NotificationType.SUBSCRIPTION_EXPIRING
            ).count(),
            0,
        )

    def test_skips_clinic_admin_with_no_email(self):
        clinic = _active_clinic_expiring_in(30)
        create_user(clinic=clinic, role="clinic_admin", email="")  # aucun e-mail renseigné

        send_subscription_expiring_notifications()  # ne doit pas lever d'exception

        self.assertEqual(
            NotificationLog.objects.filter(
                notification_type=NotificationLog.NotificationType.SUBSCRIPTION_EXPIRING
            ).count(),
            0,
        )
        clinic.refresh_from_db()
        self.assertIsNotNone(clinic.expiring_notified_30d_at)  # toujours marqué comme notifié — une tentative a eu lieu
