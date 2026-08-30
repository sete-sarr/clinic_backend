from celery import shared_task
from django.utils import timezone

from clinics.models import Clinic
from communication.models import NotificationLog
from communication.services import send_notification

# business/notification-rules.md "SUBSCRIPTION EXPIRING": 30/15/7/1 days before current_period_end.
_THRESHOLDS = [
    (30, "expiring_notified_30d_at"),
    (15, "expiring_notified_15d_at"),
    (7, "expiring_notified_7d_at"),
    (1, "expiring_notified_1d_at"),
]


@shared_task
def send_subscription_expiring_notifications():
    """Runs daily. Idempotency: a *_notified_at timestamp per threshold per clinic — same shape as
    appointments/tasks.py::send_day_before_reminders's day_before_reminder_sent_at guard, extended
    to 4 independent thresholds. A same-day rerun never double-sends because the check excludes
    clinics whose timestamp for that threshold is already set."""
    today = timezone.localdate()
    active_clinics = Clinic.objects.filter(
        subscription_status__in=[Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.PAST_DUE],
        current_period_end__isnull=False,
    )
    for clinic in active_clinics:
        days_left = (clinic.current_period_end.date() - today).days
        for threshold_days, field_name in _THRESHOLDS:
            if days_left == threshold_days and getattr(clinic, field_name) is None:
                _notify_expiring(clinic=clinic, days_left=threshold_days)
                setattr(clinic, field_name, timezone.now())
                clinic.save(update_fields=[field_name])


def _notify_expiring(*, clinic, days_left):
    # "Receptionist"-style role->users resolution — same pattern already documented in
    # docs/known-issues.md for appointments/notifications.py: role name -> all matching users in
    # the clinic, may be zero or many.
    admins = clinic.users.filter(groups__name="clinic_admin")
    subject = f"Your subscription expires in {days_left} day{'s' if days_left != 1 else ''}"
    body = (
        f"{clinic.name}'s subscription is set to renew/expire on {clinic.current_period_end.date()}. "
        f"Please ensure your payment method is up to date."
    )
    for admin_user in admins:
        if admin_user.email:
            send_notification(
                clinic=clinic, recipient_user=admin_user, channel=NotificationLog.Channel.EMAIL,
                notification_type=NotificationLog.NotificationType.SUBSCRIPTION_EXPIRING,
                recipient_address=admin_user.email, subject=subject, body=body,
            )
        # docs/known-issues.md: User has no phone field — SMS channel unreachable for
        # clinic_admin recipients today. Not fixed here, not silently worked around.
