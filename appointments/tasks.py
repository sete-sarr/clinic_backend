from datetime import timedelta

from celery import shared_task
from django.utils import timezone


@shared_task
def send_day_before_reminders():
    """Scheduled via django-celery-beat (appointments/migrations/0002_schedule_day_before_reminder.py,
    docs/business-rules.md: day-before batch reminder). Dispatches through the audited
    communication path (appointments/notifications.py::send_appointment_reminder_notifications),
    same as appointment-created/cancelled — not a direct send_mail call."""
    from subscriptions.catalog import APPOINTMENT_REMINDERS, clinics_with_feature_q

    from .models import Appointment
    from .notifications import send_appointment_reminder_notifications

    tomorrow = timezone.localdate() + timedelta(days=1)
    qs = Appointment.objects.filter(
        date=tomorrow,
        status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED],
        day_before_reminder_sent_at__isnull=True,
    ).filter(
        # Rappel groupé réservé à Professional (et à l'essai) : les rendez-vous des cliniques Starter
        # ne sont ni envoyés ni marqués, pour être rattrapés si la clinique passe à Professional.
        clinics_with_feature_q(APPOINTMENT_REMINDERS, prefix="clinic__")
    )
    for appointment in qs:
        send_appointment_reminder_notifications(appointment_id=appointment.id)
        appointment.day_before_reminder_sent_at = timezone.now()
        appointment.save(update_fields=["day_before_reminder_sent_at"])
