import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone, translation
from django.utils.dateformat import format as date_format, time_format
from django.utils.translation import gettext as _, gettext_lazy

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


PLATFORM_SIGNATURE = gettext_lazy("L'équipe Clinic Management")
_AUTOMATED_NOTICE = gettext_lazy("Ce message vous est adressé automatiquement ; merci de ne pas y répondre directement.")


# Langue des messages (docs/i18n.md §2) : celle du DESTINATAIRE, jamais celle de la requête qui
# déclenche l'envoi. Les messages sont composés dans `translation.override(<langue>)`.
def language_for_user(user) -> str:
    """Personnel : sa préférence, sinon la langue de sa clinique (User.effective_language)."""
    return getattr(user, "effective_language", "") or settings.LANGUAGE_CODE


def language_for_clinic(clinic) -> str:
    """Patients et documents : la langue de la clinique (Paramètres → Langue de la clinique)."""
    return getattr(clinic, "locale", "") or settings.LANGUAGE_CODE


def format_message_date(value) -> str:
    """Date d'un message dans la langue active : « 01/10/2026 » / « October 1, 2026 » (mois en
    toutes lettres en anglais pour lever l'ambiguïté jour/mois)."""
    if (translation.get_language() or "").startswith("en"):
        return date_format(value, "F j, Y")
    return f"{value:%d/%m/%Y}"


def format_message_time(value) -> str:
    """Heure d'un message dans la langue active : « 09h30 » / « 9:30 AM »."""
    if (translation.get_language() or "").startswith("en"):
        return time_format(value, "g:i A")
    return f"{value:%Hh%M}"


def compose_email(*, paragraphs, signature, greeting=None):
    """Mise en forme commune des e-mails (formule d'appel, paragraphes, signature, mention d'envoi
    automatique), pour une présentation professionnelle identique dans tous les modules — dans la
    langue active (translation.override du destinataire). Les SMS n'utilisent pas cette mise en
    forme : ils restent courts et préfixés par l'expéditeur."""
    return "\n\n".join([
        str(greeting or _("Bonjour,")),
        *(str(p) for p in paragraphs),
        f"{_('Cordialement,')}\n{signature}",
        str(_AUTOMATED_NOTICE),
    ])


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
        raise ValidationError(_("Un code a déjà été envoyé récemment. Veuillez patienter avant d'en demander un nouveau."))

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
    recipient_user = None if is_patient else principal
    # Un patient (pas encore de compte) reçoit le code dans la langue de sa clinique ; un
    # utilisateur, dans la sienne.
    language = language_for_clinic(clinic) if is_patient else language_for_user(principal)
    with translation.override(language):
        sender = clinic.name if clinic else str(PLATFORM_SIGNATURE)
        minutes = OtpCode.EXPIRY_MINUTES
        subject = _("Votre code de vérification — %(sender)s") % {"sender": sender}
        email_body = compose_email(
            paragraphs=[
                _("Voici votre code de vérification : %(code)s") % {"code": code},
                _(
                    "Ce code est valable %(minutes)s minutes. Ne le communiquez à personne : "
                    "nos équipes ne vous le demanderont jamais."
                ) % {"minutes": minutes},
                _("Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer ce message."),
            ],
            signature=sender,
        )
        sms_body = _(
            "%(sender)s : votre code de vérification est %(code)s (valable %(minutes)s min). "
            "Ne le communiquez à personne."
        ) % {"sender": sender, "code": code, "minutes": minutes}

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
        raise ValidationError(_("Ce code a expiré ou n'existe pas. Veuillez en demander un nouveau."))

    if not check_password(code, otp.code_hash):
        otp.attempts += 1
        if otp.attempts >= OtpCode.MAX_ATTEMPTS:
            # Consommer à la dernière tentative échouée — aucune fenêtre pour une course sur la
            # même ligne ; le demandeur doit redemander un nouveau code (limitation de débit
            # business/communication-policy.md).
            otp.consumed_at = timezone.now()
        otp.save(update_fields=["attempts", "consumed_at"])
        raise ValidationError(_("Code incorrect."))

    otp.consumed_at = timezone.now()
    otp.save(update_fields=["consumed_at"])
    return otp
