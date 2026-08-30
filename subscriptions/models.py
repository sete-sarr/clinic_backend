from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class SubscriptionEvent(TimeStampedModel):
    """Append-only history of subscription status/plan changes — NOT the current-state source of
    truth (Clinic.subscription_status/plan_tier are). Exists so a clinic's billing history survives
    independently of AuditLog's generic shape, and so webhook replays are traceable/dedupable per
    Stripe event id (Stripe explicitly documents at-least-once delivery)."""

    class Source(models.TextChoices):
        STRIPE_WEBHOOK = "stripe_webhook", "Stripe webhook"
        ADMIN_MANUAL = "admin_manual", "Manual (Django admin)"
        SYSTEM = "system", "System (trial start/expiry)"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.CASCADE, related_name="subscription_events")
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16)
    source = models.CharField(max_length=20, choices=Source.choices)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    stripe_event_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clinic", "created_at"]),
            models.Index(fields=["stripe_event_id"]),
        ]

    def __str__(self):
        return f"{self.clinic_id}: {self.from_status} -> {self.to_status} ({self.source})"
