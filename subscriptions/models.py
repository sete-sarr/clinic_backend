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
        PAYMENT = "payment", "Prepaid payment (SasPay)"

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


class SubscriptionPayment(TimeStampedModel):
    """Paiement d'une période d'abonnement prépayée (SasPay, Mobile Money ou carte — décision du
    2026-10-14). Une ligne par tentative de paiement ; seule une ligne Payée prolonge l'accès
    (subscriptions/prepaid.py). `amount` = prix de la formule, hors frais (payés en plus par la
    clinique) ; `charged_amount` = montant effectivement débité, frais compris."""

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        PAID = "paid", "Payé"
        FAILED = "failed", "Échoué"
        EXPIRED = "expired", "Expiré"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="subscription_payments")
    plan_tier = models.CharField(max_length=16)
    billing_cycle = models.CharField(max_length=8)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="XOF")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider = models.CharField(max_length=20, default="saspay")
    provider_session_id = models.CharField(max_length=64, blank=True, db_index=True)
    provider_transaction_id = models.CharField(max_length=64, blank=True, db_index=True)
    provider_reference = models.CharField(max_length=64, blank=True)
    checkout_url = models.URLField(max_length=500, blank=True)
    charged_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    network = models.CharField(max_length=40, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["clinic", "status"])]

    def __str__(self):
        return f"{self.clinic_id} {self.plan_tier}/{self.billing_cycle} {self.amount} {self.status}"
