"""Abonnement prépayé par SasPay (décisions de l'utilisateur du 2026-10-14) — la clinique paie une
période (1 ou 12 mois) d'avance par Mobile Money ou carte ; aucun prélèvement automatique.

Règles :
- prix de la formule hors frais (catalog.plan_price_xof) ; les frais SasPay sont payés en plus par la
  clinique (mode ADD_ON) ;
- même formule, période en cours ou en délai de grâce : la nouvelle période s'ajoute à la fin de la
  précédente (payer en avance ne fait rien perdre) ;
- pendant l'essai : la période commence à la fin de l'essai (les jours gratuits restants sont gardés) ;
- autre formule (ex. Starter -> Professional) : la nouvelle période commence le jour du paiement,
  les jours restants de l'ancienne formule sont perdus (option B, annoncée à l'écran) ;
- un paiement n'active rien tant qu'il n'est pas revérifié auprès de SasPay (statut SUCCESS, montant
  et devise attendus) ; il n'est appliqué qu'une fois (verrou de ligne + statut) ;
- échéance dépassée : Paiement en retard pendant GRACE_DAYS jours, puis Suspendu (lecture seule).
"""

import calendar
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog

from . import saspay
from .catalog import GRACE_DAYS, PERIOD_MONTHS, SUBSCRIBABLE_TIERS, plan_price_xof
from .models import SubscriptionEvent, SubscriptionPayment

logger = logging.getLogger(__name__)

# Une session de paiement non réglée est abandonnée après ce délai (vérifications de rattrapage).
PENDING_LIFETIME = timedelta(hours=48)
_PLAN_LABELS = {Clinic.PlanTier.STARTER: "Starter", Clinic.PlanTier.PROFESSIONAL: "Professional"}


def add_months(moment, months):
    """Même jour, `months` mois plus tard (ramené au dernier jour du mois si besoin : 31/01 -> 28/02)."""
    month_index = moment.month - 1 + months
    year, month = moment.year + month_index // 12, month_index % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def start_prepaid_checkout(*, clinic, plan_tier, billing_cycle, actor):
    """Crée le paiement en attente et la page de paiement SasPay ; renvoie le SubscriptionPayment.
    Le montant est toujours calculé ici, jamais reçu du navigateur."""
    if plan_tier not in SUBSCRIBABLE_TIERS:
        raise ValidationError(_("Cette formule n'est pas disponible à la souscription."))
    if billing_cycle not in Clinic.BillingCycle.values:
        raise ValidationError(_("Cycle de facturation invalide."))
    payment = SubscriptionPayment.objects.create(
        clinic=clinic, plan_tier=plan_tier, billing_cycle=billing_cycle, created_by=actor,
        amount=Decimal(plan_price_xof(plan_tier, billing_cycle)), currency="XOF",
    )
    cycle = _("annuel") if billing_cycle == Clinic.BillingCycle.ANNUAL else _("mensuel")
    try:
        session = saspay.create_checkout_session(
            amount=payment.amount,
            customer_email=actor.email or clinic.email,
            customer_name=clinic.name,
            description=_("Abonnement proCli %(plan)s (%(cycle)s) — %(clinic)s")
            % {"plan": _PLAN_LABELS.get(plan_tier, plan_tier), "cycle": cycle, "clinic": clinic.name},
            return_url=f"{settings.FRONTEND_BASE_URL}/subscription?payment={payment.pk}",
            metadata={"clinic_id": clinic.pk, "subscription_payment_id": payment.pk},
        )
    except saspay.SasPayError as exc:
        payment.status = SubscriptionPayment.Status.FAILED
        payment.save(update_fields=["status", "updated_at"])
        raise ValidationError(_("Le paiement en ligne est momentanément indisponible. Réessayez plus tard.")) from exc
    payment.provider_session_id = session.id
    payment.checkout_url = session.checkout_url
    payment.save(update_fields=["provider_session_id", "checkout_url", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=payment,
                 metadata={"clinic_id": clinic.pk, "plan_tier": plan_tier, "billing_cycle": billing_cycle})
    return payment


def refresh_payment(payment):
    """Revérifie un paiement en attente auprès de SasPay et l'applique s'il est réglé. Renvoie le
    paiement (statut à jour). Sans effet sur un paiement déjà traité."""
    if payment.status != SubscriptionPayment.Status.PENDING or not payment.provider_session_id:
        return payment
    try:
        session = saspay.get_session_status(payment.provider_session_id)
        if session.status == "PAID" and session.transaction_id:
            return confirm_payment(payment, saspay.verify_transaction(session.transaction_id))
    except saspay.SasPayError:
        return payment  # nouvelle tentative au prochain rafraîchissement ou par la tâche quotidienne
    if payment.created_at < timezone.now() - PENDING_LIFETIME:
        SubscriptionPayment.objects.filter(pk=payment.pk, status=SubscriptionPayment.Status.PENDING).update(
            status=SubscriptionPayment.Status.EXPIRED, updated_at=timezone.now()
        )
        payment.refresh_from_db()
    return payment


@transaction.atomic
def confirm_payment(payment, txn):
    """Applique une transaction SasPay revérifiée. Idempotent : un second appel ne prolonge rien."""
    payment = SubscriptionPayment.objects.select_for_update().select_related("clinic").get(pk=payment.pk)
    if payment.status == SubscriptionPayment.Status.PAID:
        return payment
    if txn.status == "FAILED":
        payment.status = SubscriptionPayment.Status.FAILED
        payment.provider_transaction_id = txn.id
        payment.save(update_fields=["status", "provider_transaction_id", "updated_at"])
        return payment
    if txn.status != "SUCCESS":
        return payment
    if txn.currency != payment.currency or Decimal(txn.requested_amount) != payment.amount:
        # Ne devrait jamais arriver : le montant est fixé côté serveur. On n'active rien.
        logger.error("Paiement d'abonnement %s : montant ou devise inattendus (%s %s)", payment.pk,
                     txn.requested_amount, txn.currency)
        return payment

    clinic = Clinic.objects.select_for_update().get(pk=payment.clinic_id)
    now = timezone.now()
    start = _period_start(clinic, payment, now)
    end = add_months(start, PERIOD_MONTHS[payment.billing_cycle])

    payment.status = SubscriptionPayment.Status.PAID
    payment.provider_transaction_id = txn.id
    payment.provider_reference = txn.reference
    payment.network = txn.network
    payment.charged_amount = Decimal(txn.debited_amount)
    payment.paid_at = now
    payment.period_start, payment.period_end = start, end
    payment.save()

    from .services import change_plan, change_subscription_status

    if (clinic.plan_tier, clinic.billing_cycle) != (payment.plan_tier, payment.billing_cycle):
        change_plan(clinic=clinic, plan_tier=payment.plan_tier, billing_cycle=payment.billing_cycle,
                    changed_by=None, metadata={"source": SubscriptionEvent.Source.PAYMENT, "payment_id": payment.pk})
    clinic.current_period_end = end
    clinic.expiring_notified_30d_at = clinic.expiring_notified_15d_at = None
    clinic.expiring_notified_7d_at = clinic.expiring_notified_3d_at = clinic.expiring_notified_1d_at = None
    clinic.save(update_fields=[
        "current_period_end", "expiring_notified_30d_at", "expiring_notified_15d_at",
        "expiring_notified_7d_at", "expiring_notified_3d_at", "expiring_notified_1d_at", "updated_at",
    ])
    change_subscription_status(
        clinic=clinic, status=Clinic.SubscriptionStatus.ACTIVE, changed_by=None,
        source=SubscriptionEvent.Source.PAYMENT,
        metadata={"clinic_id": clinic.pk, "payment_id": payment.pk, "period_end": end.isoformat()},
    )
    transaction.on_commit(lambda: _notify_payment_received(payment.pk))
    return payment


def _period_start(clinic, payment, now):
    same_plan = (clinic.plan_tier, clinic.billing_cycle) == (payment.plan_tier, payment.billing_cycle)
    if clinic.subscription_status == Clinic.SubscriptionStatus.TRIAL:
        return max(now, clinic.trial_ends_at or now)
    if (
        same_plan
        and clinic.subscription_status in (Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.PAST_DUE)
        and clinic.current_period_end
        and clinic.current_period_end + timedelta(days=GRACE_DAYS) > now
    ):
        return max(now, clinic.current_period_end)
    return now


def handle_webhook(*, event, data):
    """Webhook SasPay vérifié (signature et date) : transaction.success / transaction.failed. Le
    paiement est retrouvé par sa transaction, sinon parmi les sessions en attente, puis toujours
    revérifié auprès de SasPay avant d'être appliqué."""
    if event not in ("transaction.success", "transaction.failed"):
        return
    transaction_id = str((data or {}).get("id") or "")
    if not transaction_id:
        return
    payment = SubscriptionPayment.objects.filter(provider_transaction_id=transaction_id).first()
    candidates = [payment] if payment else SubscriptionPayment.objects.filter(
        status=SubscriptionPayment.Status.PENDING, created_at__gte=timezone.now() - PENDING_LIFETIME,
    ).exclude(provider_session_id="")
    for candidate in candidates:
        try:
            session = saspay.get_session_status(candidate.provider_session_id)
            if session.transaction_id != transaction_id:
                continue
            confirm_payment(candidate, saspay.verify_transaction(transaction_id))
        except saspay.SasPayError:
            logger.warning("Webhook SasPay %s : vérification impossible, rattrapage par la tâche quotidienne",
                           transaction_id)
        return


def refresh_pending_payments():
    """Tâche quotidienne : rattrape les paiements réglés dont le webhook aurait été perdu."""
    pending = SubscriptionPayment.objects.filter(status=SubscriptionPayment.Status.PENDING)
    for payment in pending:
        refresh_payment(payment)
    return pending.count()


def expire_prepaid_periods(now=None):
    """Tâche quotidienne : échéance passée -> Paiement en retard ; délai de grâce passé -> Suspendu
    (lecture seule, e-mail aux administrateurs). Jamais pour un abonnement Stripe ni la démo."""
    from .services import change_subscription_status

    now = now or timezone.now()
    prepaid = Clinic.objects.filter(stripe_subscription_id="", is_demo=False, current_period_end__isnull=False)
    changed = 0
    for clinic in prepaid.filter(subscription_status=Clinic.SubscriptionStatus.ACTIVE, current_period_end__lte=now):
        change_subscription_status(clinic=clinic, status=Clinic.SubscriptionStatus.PAST_DUE, changed_by=None,
                                   source=SubscriptionEvent.Source.SYSTEM,
                                   metadata={"clinic_id": clinic.pk, "reason": "period_ended"})
        changed += 1
    grace_limit = now - timedelta(days=GRACE_DAYS)
    for clinic in prepaid.filter(subscription_status=Clinic.SubscriptionStatus.PAST_DUE,
                                 current_period_end__lte=grace_limit):
        change_subscription_status(clinic=clinic, status=Clinic.SubscriptionStatus.SUSPENDED, changed_by=None,
                                   source=SubscriptionEvent.Source.SYSTEM,
                                   metadata={"clinic_id": clinic.pk, "reason": "grace_period_ended"})
        changed += 1
    return changed


def _notify_payment_received(payment_id):
    """Confirmation par e-mail aux administrateurs de la clinique (business/notification-rules.md)."""
    from communication.models import NotificationLog
    from communication.services import (
        PLATFORM_SIGNATURE, compose_email, format_message_date, language_for_user, send_notification,
    )
    from common.currency import format_money

    payment = SubscriptionPayment.objects.select_related("clinic").get(pk=payment_id)
    clinic = payment.clinic
    for admin_user in clinic.users.filter(groups__name="clinic_admin", is_active=True):
        if not admin_user.email:
            continue
        with translation.override(language_for_user(admin_user)):
            values = {
                "clinic": clinic.name, "plan": _PLAN_LABELS.get(payment.plan_tier, payment.plan_tier),
                "amount": format_money(payment.charged_amount or payment.amount, payment.currency),
                "end": format_message_date(timezone.localtime(payment.period_end).date()),
            }
            subject = _("Paiement reçu — abonnement %(plan)s de %(clinic)s") % values
            body = compose_email(paragraphs=[
                _("Nous avons bien reçu votre paiement de %(amount)s pour la formule %(plan)s.") % values,
                _("L'abonnement de %(clinic)s est actif jusqu'au %(end)s. Vous pourrez le prolonger à tout "
                  "moment depuis l'écran « Abonnement ».") % values,
            ], signature=PLATFORM_SIGNATURE)
        send_notification(
            clinic=clinic, recipient_user=admin_user, channel=NotificationLog.Channel.EMAIL,
            notification_type=NotificationLog.NotificationType.SUBSCRIPTION_PAYMENT,
            recipient_address=admin_user.email, subject=subject, body=body,
        )
