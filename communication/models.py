from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class NotificationLog(TimeStampedModel):
    """
    Every outbound email/SMS goes through here — audit trail and the Celery task's unit of
    work (docs/communication-architecture.md: full audit trail, no module talks to a
    provider directly). `deliver_notification` retries against this row's id, not raw
    params, so retries are idempotent.
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
    Belongs to a `User` OR a `Patient` (never both) — activation-request has to send an OTP
    to a Patient that has no linked User yet. See communication/services.py for the
    generate_and_send_otp/verify_otp entry points; the code is never stored in plaintext.
    """

    class Purpose(models.TextChoices):
        ACCOUNT_ACTIVATION = "account_activation", "Account activation"

    MAX_ATTEMPTS = 5
    COOLDOWN_SECONDS = 60
    EXPIRY_MINUTES = 5  # business/notification-rules.md "OTP GENERATED: Expiration: 5 minutes"

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
