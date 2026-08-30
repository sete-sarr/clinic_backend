from datetime import timedelta

from celery import shared_task
from django.utils import timezone


@shared_task
def send_day_before_reminders():
    """Scheduled via django-celery-beat (appointments/migrations/0002_schedule_day_before_reminder.py,
    docs/business-rules.md: day-before batch reminder). Dispatches through the audited
    communication path (appointments/notifications.py::send_appointment_reminder_notifications),
    same as appointment-created/cancelled — not a direct send_mail call."""
    from .models import Appointment
    from .notifications import send_appointment_reminder_notifications

    tomorrow = timezone.localdate() + timedelta(days=1)
    qs = Appointment.objects.filter(
        date=tomorrow,
        status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
        day_before_reminder_sent_at__isnull=True,
    )
    for appointment in qs:
        send_appointment_reminder_notifications(appointment_id=appointment.id)
        appointment.day_before_reminder_sent_at = timezone.now()
        appointment.save(update_fields=["day_before_reminder_sent_at"])
