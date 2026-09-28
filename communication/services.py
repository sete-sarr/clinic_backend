import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import NotificationLog, OtpCode


def send_notification(*, clinic, recipient_user, channel, notification_type, recipient_address, subject, body):
    """Le seul point d'entrée que les autres apps doivent appeler pour envoyer quoi que ce soit
    (docs/communication-architecture.md : "aucun module ne peut communiquer directement avec les
    fournisseurs Email, SMS ou Push"). Persiste d'abord un NotificationLog (piste d'audit) puis
    confie l'envoi effectif à une tâche Celery."""
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


PLATFORM_SIGNATURE = "L'équipe Clinic Management"
_AUTOMATED_NOTICE = "Ce message vous est adressé automatiquement ; merci de ne pas y répondre directement."


def compose_email(*, paragraphs, signature, greeting="Bonjour,"):
    """Mise en forme commune des e-mails (formule d'appel, paragraphes, signature, mention d'envoi
    automatique), pour une présentation professionnelle identique dans tous les modules. Les SMS
    n'utilisent pas cette mise en forme : ils restent courts et préfixés par l'expéditeur."""
    return "\n\n".join([greeting, *paragraphs, f"Cordialement,\n{signature}", _AUTOMATED_NOTICE])


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _principal_filter(*, principal, purpose):
    is_patient = hasattr(principal, "patient_number")
    return {"patient": principal, "purpose": purpose} if is_patient else {"user": principal, "purpose": purpose}


def generate_and_send_otp(*, principal, purpose):
    """`principal` est une instance de `User` ou de `Patient` (duck-typing sur `.email`/`.phone`) —
    l'activation envoie un OTP à un Patient qui n'a pas encore de User lié.
    business/notification-rules.md "OTP GÉNÉRÉ" : envoyé à la fois par SMS et par Email,
    expiration de 5 minutes. business/communication-policy.md : "génération d'OTP limitée" —
    appliqué ici via un délai de rafraîchissement (cooldown) par principal."""
    filter_kwargs = _principal_filter(principal=principal, purpose=purpose)

    cooldown_cutoff = timezone.now() - timedelta(seconds=OtpCode.COOLDOWN_SECONDS)
    already_sent_recently = OtpCode.objects.filter(
        **filter_kwargs, consumed_at__isnull=True, created_at__gte=cooldown_cutoff
    ).exists()
    if already_sent_recently:
        raise ValidationError("Un code a déjà été envoyé récemment. Veuillez patienter avant d'en demander un nouveau.")

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
    sender = clinic.name if clinic else PLATFORM_SIGNATURE
    subject = f"Votre code de vérification — {sender}"
    email_body = compose_email(
        paragraphs=[
            f"Voici votre code de vérification : {code}",
            f"Ce code est valable {OtpCode.EXPIRY_MINUTES} minutes. Ne le communiquez à personne : "
            "nos équipes ne vous le demanderont jamais.",
            "Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer ce message.",
        ],
        signature=sender,
    )
    sms_body = (
        f"{sender} : votre code de vérification est {code} (valable {OtpCode.EXPIRY_MINUTES} min). "
        "Ne le communiquez à personne."
    )
    recipient_user = None if is_patient else principal

    if email:
        send_notification(
            clinic=clinic,
            recipient_user=recipient_user,
            channel=NotificationLog.Channel.EMAIL,
            notification_type=NotificationLog.NotificationType.OTP,
            recipient_address=email,
            subject=subject,
            body=email_body,
        )
    if phone:
        send_notification(
            clinic=clinic,
            recipient_user=recipient_user,
            channel=NotificationLog.Channel.SMS,
            notification_type=NotificationLog.NotificationType.OTP,
            recipient_address=phone,
            subject=subject,
            body=sms_body,
        )
    return otp


def verify_otp(*, principal, code, purpose):
    filter_kwargs = _principal_filter(principal=principal, purpose=purpose)

    otp = OtpCode.objects.filter(**filter_kwargs, consumed_at__isnull=True).order_by("-created_at").first()
    if not otp or otp.expires_at < timezone.now():
        raise ValidationError("Ce code a expiré ou n'existe pas. Veuillez en demander un nouveau.")

    if not check_password(code, otp.code_hash):
        otp.attempts += 1
        if otp.attempts >= OtpCode.MAX_ATTEMPTS:
            # Consommer à la dernière tentative échouée — aucune fenêtre pour une course sur la
            # même ligne ; le demandeur doit redemander un nouveau code (limitation de débit
            # business/communication-policy.md).
            otp.consumed_at = timezone.now()
        otp.save(update_fields=["attempts", "consumed_at"])
        raise ValidationError("Incorrect code.")

    otp.consumed_at = timezone.now()
    otp.save(update_fields=["consumed_at"])
    return otp
