import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import NotificationLog, OtpCode


def send_notification(*, clinic, recipient_user, channel, notification_type, recipient_address, subject, body):
    """The only entry point other apps should call to send anything (docs/communication-architecture.md:
    "no module may directly communicate with Email, SMS or Push providers"). Persists a
    NotificationLog first (audit trail) and hands the actual delivery to a Celery task."""
    log = NotificationLog.objects.create(
        clinic=clinic,
        recipient_user=recipient_user,
        channel=channel,
        notification_type=notification_type,
        recipient_address=recipient_address,
        subject=subject,
        body=body,
    )
    from .tasks import deliver_notification

    transaction.on_commit(lambda: deliver_notification.delay(log.id))
    return log


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _principal_filter(*, principal, purpose):
    is_patient = hasattr(principal, "patient_number")
    return {"patient": principal, "purpose": purpose} if is_patient else {"user": principal, "purpose": purpose}


def generate_and_send_otp(*, principal, purpose):
    """`principal` is a `User` or `Patient` instance (duck-typed on `.email`/`.phone`) — activation
    sends an OTP to a Patient with no linked User yet. business/notification-rules.md "OTP GENERATED":
    sent on both SMS and Email, 5-minute expiry. business/communication-policy.md: "OTP generation
    limited" — enforced here via a per-principal cooldown."""
    filter_kwargs = _principal_filter(principal=principal, purpose=purpose)

    cooldown_cutoff = timezone.now() - timedelta(seconds=OtpCode.COOLDOWN_SECONDS)
    already_sent_recently = OtpCode.objects.filter(
        **filter_kwargs, consumed_at__isnull=True, created_at__gte=cooldown_cutoff
    ).exists()
    if already_sent_recently:
        raise ValidationError("A code was already sent recently. Please wait before requesting another one.")

    code = _generate_code()
    clinic = getattr(principal, "clinic", None)
    is_patient = "patient" in filter_kwargs
    otp = OtpCode.objects.create(
        **filter_kwargs,
        clinic=clinic,
        code_hash=make_password(code),
        expires_at=timezone.now() + timedelta(minutes=OtpCode.EXPIRY_MINUTES),
    )

    email = getattr(principal, "email", "") or ""
    phone = getattr(principal, "phone", "") or ""
    subject = "Your verification code"
    body = f"Your verification code is {code}. It expires in {OtpCode.EXPIRY_MINUTES} minutes."
    recipient_user = None if is_patient else principal

    if email:
        send_notification(
            clinic=clinic,
            recipient_user=recipient_user,
            channel=NotificationLog.Channel.EMAIL,
            notification_type=NotificationLog.NotificationType.OTP,
            recipient_address=email,
            subject=subject,
            body=body,
        )
    if phone:
        send_notification(
            clinic=clinic,
            recipient_user=recipient_user,
            channel=NotificationLog.Channel.SMS,
            notification_type=NotificationLog.NotificationType.OTP,
            recipient_address=phone,
            subject=subject,
            body=body,
        )
    return otp


def verify_otp(*, principal, code, purpose):
    filter_kwargs = _principal_filter(principal=principal, purpose=purpose)

    otp = OtpCode.objects.filter(**filter_kwargs, consumed_at__isnull=True).order_by("-created_at").first()
    if not otp or otp.expires_at < timezone.now():
        raise ValidationError("This code has expired or does not exist. Please request a new one.")

    if not check_password(code, otp.code_hash):
        otp.attempts += 1
        if otp.attempts >= OtpCode.MAX_ATTEMPTS:
            # Consume on the final failed attempt — no window for a race against the same row;
            # the requester must request a fresh code (business/communication-policy.md rate limiting).
            otp.consumed_at = timezone.now()
        otp.save(update_fields=["attempts", "consumed_at"])
        raise ValidationError("Incorrect code.")

    otp.consumed_at = timezone.now()
    otp.save(update_fields=["consumed_at"])
    return otp
