from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class NotificationLog(TimeStampedModel):
    """
    Chaque e-mail/SMS sortant passe par ici — piste d'audit et unité de travail de la tâche
    Celery (docs/communication-architecture.md : piste d'audit complète, aucun module ne
    parle directement à un fournisseur). `deliver_notification` relance en se basant sur
    l'id de cette ligne, pas sur les paramètres bruts, donc les nouvelles tentatives sont
    idempotentes.
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    class NotificationType(models.TextChoices):
        OTP = "otp", "OTP"
        ACCOUNT_ACTIVATION = "account_activation", "Account activation"
        EMAIL_VERIFICATION = "email_verification", "Email verification"
        APPOINTMENT_CREATED = "appointment_created", "Appointment created"
        APPOINTMENT_MODIFIED = "appointment_modified", "Appointment modified"
        APPOINTMENT_CANCELLED = "appointment_cancelled", "Appointment cancelled"
        APPOINTMENT_REMINDER = "appointment_reminder", "Appointment reminder"
        SUBSCRIPTION_EXPIRING = "subscription_expiring", "Subscription expiring"
        LICENSE_EXPIRED = "license_expired", "License expired"
        STOCK_LOW = "stock_low", "Low stock"
        STOCK_OVERSTOCK = "stock_overstock", "Overstock"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.SET_NULL, null=True, blank=True, related_name="notification_logs"
    )
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="notification_logs"
    )
    channel = models.CharField(max_length=10, choices=Channel.choices)
    notification_type = models.CharField(max_length=30, choices=NotificationType.choices)
    recipient_address = models.CharField(max_length=255)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_name = models.CharField(max_length=50, blank=True)
    error_message = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clinic", "created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.channel}:{self.notification_type} to {self.recipient_address} ({self.status})"


class OtpCode(TimeStampedModel):
    """
    Appartient à un `User` OU à un `Patient` (jamais les deux) — la demande d'activation doit
    pouvoir envoyer un OTP à un Patient qui n'a pas encore de User lié. Voir
    communication/services.py pour les points d'entrée generate_and_send_otp/verify_otp ; le
    code n'est jamais stocké en clair.
    """

    class Purpose(models.TextChoices):
        ACCOUNT_ACTIVATION = "account_activation", "Account activation"

    MAX_ATTEMPTS = 5
    COOLDOWN_SECONDS = 60
    EXPIRY_MINUTES = 5  # business/notification-rules.md "OTP GÉNÉRÉ : Expiration : 5 minutes"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="otp_codes"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE, null=True, blank=True, related_name="otp_codes"
    )
    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.SET_NULL, null=True, blank=True)
    purpose = models.CharField(max_length=30, choices=Purpose.choices)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(user__isnull=False, patient__isnull=True)
                    | models.Q(user__isnull=True, patient__isnull=False)
                ),
                name="otp_exactly_one_principal",
            )
        ]
        indexes = [models.Index(fields=["patient", "purpose"]), models.Index(fields=["user", "purpose"])]

    def __str__(self):
        principal = self.user or self.patient
        return f"OTP({self.purpose}) for {principal}"
