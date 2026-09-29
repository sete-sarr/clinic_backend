from django.db import transaction
from django.utils.translation import gettext as _

from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog

from .catalog import SUBSCRIBABLE_TIERS, TRIAL_DAYS, plan_for_stripe_price_id
from .models import SubscriptionEvent

# Cycle de vie stabilisé (business/subscription-billing-policy.md) : Trial -> Active -> Past Due ->
# Suspended -> Cancelled, chacun des états Trial/PastDue/Suspended pouvant court-circuiter vers Cancelled.
# Trial -> Suspended : fin de l'essai gratuit sans souscription (décision métier du 2026-09-28, voir
# expire_trials()).
_ALLOWED_TRANSITIONS = {
    Clinic.SubscriptionStatus.TRIAL: {
        Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.SUSPENDED, Clinic.SubscriptionStatus.CANCELLED,
    },
    Clinic.SubscriptionStatus.ACTIVE: {Clinic.SubscriptionStatus.PAST_DUE, Clinic.SubscriptionStatus.CANCELLED},
    Clinic.SubscriptionStatus.PAST_DUE: {Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.SUSPENDED},
    Clinic.SubscriptionStatus.SUSPENDED: {Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.CANCELLED},
    Clinic.SubscriptionStatus.CANCELLED: set(),
}


def start_trial(*, clinic: Clinic, trial_days: int = TRIAL_DAYS) -> Clinic:
    """Point d'accroche pour l'endroit où le provisioning de clinique aura lieu (le provisioning de
    tenant lui-même n'existe pas encore dans cette base de code — hors périmètre ici)."""
    from datetime import timedelta

    from django.utils import timezone

    clinic.subscription_status = Clinic.SubscriptionStatus.TRIAL
    clinic.trial_ends_at = timezone.now() + timedelta(days=trial_days)
    clinic.save(update_fields=["subscription_status", "trial_ends_at", "updated_at"])
    SubscriptionEvent.objects.create(
        clinic=clinic, from_status="", to_status=Clinic.SubscriptionStatus.TRIAL,
        source=SubscriptionEvent.Source.SYSTEM,
    )
    return clinic


@transaction.atomic
def change_subscription_status(
    *, clinic: Clinic, status: str, changed_by, source: str, metadata: dict | None = None,
    stripe_event_id: str = "",
) -> Clinic:
    """La SEULE fonction autorisée à écrire Clinic.subscription_status. Valide la transition, la
    persiste, écrit une ligne SubscriptionEvent, et appelle record_audit en plaçant clinic_id
    explicitement dans les métadonnées (docs/known-issues.md : record_audit résout AuditLog.clinic
    via obj.clinic ou user.clinic, tous deux None/incorrects pour un audit de Clinic déclenché par
    un superutilisateur ou un webhook — ceci est une mitigation documentée, pas une correction du
    helper partagé)."""
    from_status = clinic.subscription_status
    if status != from_status and status not in _ALLOWED_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"Illegal subscription transition: {from_status} -> {status}")

    clinic.subscription_status = status
    clinic.save(update_fields=["subscription_status", "updated_at"])

    SubscriptionEvent.objects.create(
        clinic=clinic, from_status=from_status, to_status=status, source=source,
        changed_by=changed_by if getattr(changed_by, "is_authenticated", False) else None,
        stripe_event_id=stripe_event_id, metadata=metadata or {},
    )
    record_audit(
        user=changed_by, action=AuditLog.Action.UPDATE, obj=clinic,
        metadata={**(metadata or {}), "clinic_id": clinic.pk, "from_status": from_status, "to_status": status},
    )

    if status == Clinic.SubscriptionStatus.SUSPENDED and status != from_status:
        _notify_license_expired(clinic=clinic, trial_ended=from_status == Clinic.SubscriptionStatus.TRIAL)

    return clinic


def change_plan(*, clinic: Clinic, plan_tier: str, billing_cycle: str, changed_by, metadata: dict | None = None) -> Clinic:
    """Changements de plan/cycle indépendants des changements de statut (ex. mise à niveau alors
    que le statut est Active)."""
    from_plan, from_cycle = clinic.plan_tier, clinic.billing_cycle
    clinic.plan_tier = plan_tier
    clinic.billing_cycle = billing_cycle
    clinic.save(update_fields=["plan_tier", "billing_cycle", "updated_at"])
    record_audit(
        user=changed_by, action=AuditLog.Action.UPDATE, obj=clinic,
        metadata={
            **(metadata or {}), "clinic_id": clinic.pk,
            "from_plan": from_plan, "to_plan": plan_tier,
            "from_cycle": from_cycle, "to_cycle": billing_cycle,
        },
    )
    return clinic


def start_checkout(*, clinic: Clinic, plan_tier: str, billing_cycle: str, success_url: str, cancel_url: str):
    """Relais vers le PaymentProvider résolu — le seul point que api/views.py peut appeler pour
    créer une Checkout Session. Seules les formules de SUBSCRIBABLE_TIERS sont proposées."""
    from .providers import get_payment_provider
    from .providers.base import CheckoutSessionResult

    if plan_tier not in SUBSCRIBABLE_TIERS:
        return CheckoutSessionResult(
            success=False, provider_name="", error_message=_("Cette formule n'est pas disponible à la souscription.")
        )
    if billing_cycle not in Clinic.BillingCycle.values:
        return CheckoutSessionResult(success=False, provider_name="", error_message=_("Cycle de facturation invalide."))
    # Un abonnement Stripe existe déjà : une nouvelle Checkout Session en créerait un second (double
    # prélèvement). Le changement de formule passe par change_subscribed_plan, le règlement d'un
    # impayé par le portail client.
    existing_error = _existing_subscription_error(clinic)
    if existing_error:
        return CheckoutSessionResult(success=False, provider_name="", error_message=existing_error)

    return get_payment_provider().create_checkout_session(
        clinic=clinic, plan_tier=plan_tier, billing_cycle=billing_cycle,
        success_url=success_url, cancel_url=cancel_url,
    )


_LIVE_SUBSCRIPTION_STATUSES = (
    Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.PAST_DUE, Clinic.SubscriptionStatus.SUSPENDED,
)


def _existing_subscription_error(clinic: Clinic) -> str:
    if not clinic.stripe_subscription_id or clinic.subscription_status not in _LIVE_SUBSCRIPTION_STATUSES:
        return ""
    if clinic.subscription_status == Clinic.SubscriptionStatus.ACTIVE:
        return _("Votre clinique a déjà un abonnement en cours : changez de formule au lieu d'en souscrire une nouvelle.")
    return _("Un paiement de votre abonnement est en attente : réglez-le depuis « Gérer l'abonnement ».")


def change_subscribed_plan(*, clinic: Clinic, plan_tier: str, billing_cycle: str, actor):
    """Change la formule d'une clinique déjà abonnée en modifiant son abonnement Stripe existant
    (jamais un second abonnement). Réservé au statut Actif ; un impayé se règle d'abord via le
    portail client."""
    from .providers import get_payment_provider
    from .providers.base import PlanChangeResult

    def _failure(message):
        return PlanChangeResult(success=False, provider_name="", error_message=message)

    if plan_tier not in SUBSCRIBABLE_TIERS:
        return _failure(_("Cette formule n'est pas disponible à la souscription."))
    if billing_cycle not in Clinic.BillingCycle.values:
        return _failure(_("Cycle de facturation invalide."))
    if not clinic.stripe_subscription_id or clinic.subscription_status not in _LIVE_SUBSCRIPTION_STATUSES:
        return _failure(_("Aucun abonnement en cours : choisissez une formule pour souscrire."))
    if clinic.subscription_status != Clinic.SubscriptionStatus.ACTIVE:
        return _failure(_("Un paiement de votre abonnement est en attente : réglez-le depuis « Gérer l'abonnement »."))
    if (plan_tier, billing_cycle) == (clinic.plan_tier, clinic.billing_cycle):
        return _failure(_("C'est déjà votre formule actuelle."))

    result = get_payment_provider().change_subscription_plan(
        clinic=clinic, plan_tier=plan_tier, billing_cycle=billing_cycle
    )
    if result.success:
        change_plan(
            clinic=clinic, plan_tier=plan_tier, billing_cycle=billing_cycle, changed_by=actor,
            metadata={"source": "plan_change"},
        )
    return result


def start_billing_portal_session(*, clinic: Clinic, return_url: str):
    from .providers import get_payment_provider

    return get_payment_provider().create_billing_portal_session(clinic=clinic, return_url=return_url)


def _resolve_clinic(payload: dict) -> Clinic | None:
    clinic_id = (payload.get("metadata") or {}).get("clinic_id") or payload.get("client_reference_id")
    customer_id = payload.get("customer")
    if clinic_id:
        return Clinic.objects.filter(pk=clinic_id).first()
    if customer_id:
        return Clinic.objects.filter(stripe_customer_id=customer_id).first()
    return None


def _on_checkout_completed(*, event_id, payload):
    clinic = _resolve_clinic(payload)
    if not clinic:
        return
    clinic.stripe_customer_id = payload.get("customer") or clinic.stripe_customer_id
    clinic.stripe_subscription_id = payload.get("subscription") or clinic.stripe_subscription_id
    clinic.save(update_fields=["stripe_customer_id", "stripe_subscription_id"])
    metadata = payload.get("metadata") or {}
    plan_tier, billing_cycle = metadata.get("plan_tier"), metadata.get("billing_cycle")
    if plan_tier in Clinic.PlanTier.values and billing_cycle in Clinic.BillingCycle.values:
        if (plan_tier, billing_cycle) != (clinic.plan_tier, clinic.billing_cycle):
            change_plan(
                clinic=clinic, plan_tier=plan_tier, billing_cycle=billing_cycle, changed_by=None,
                metadata={"source": SubscriptionEvent.Source.STRIPE_WEBHOOK, "stripe_event_id": event_id},
            )
    change_subscription_status(
        clinic=clinic, status=Clinic.SubscriptionStatus.ACTIVE, changed_by=None,
        source=SubscriptionEvent.Source.STRIPE_WEBHOOK, stripe_event_id=event_id,
        metadata={"clinic_id": clinic.pk},
    )


_STRIPE_STATUS_MAP = {
    "active": Clinic.SubscriptionStatus.ACTIVE,
    # Une clinique qui souscrit pendant son essai garde ses jours gratuits restants côté Stripe
    # (trial_end, voir StripePaymentProvider) : l'abonnement Stripe est alors "trialing" mais la
    # clinique a bien souscrit (moyen de paiement enregistré) — elle est Active ici, et n'est donc
    # jamais suspendue par expire_trials() qui ne vise que les essais sans souscription.
    "trialing": Clinic.SubscriptionStatus.ACTIVE,
    "past_due": Clinic.SubscriptionStatus.PAST_DUE,
    "unpaid": Clinic.SubscriptionStatus.SUSPENDED,
    "canceled": Clinic.SubscriptionStatus.CANCELLED,
}


def _on_subscription_updated(*, event_id, payload):
    clinic = _resolve_clinic(payload)
    if not clinic:
        return

    period_end = payload.get("current_period_end")
    if period_end:
        from datetime import datetime, timezone as dt_timezone

        clinic.current_period_end = datetime.fromtimestamp(period_end, tz=dt_timezone.utc)
        # Renouvellement — réinitialise le verrou d'idempotence à 4 paliers des notifications
        # d'expiration afin que les rappels du prochain cycle se déclenchent à nouveau, au lieu de
        # rester indéfiniment marqués "déjà notifié".
        clinic.expiring_notified_30d_at = None
        clinic.expiring_notified_15d_at = None
        clinic.expiring_notified_7d_at = None
        clinic.expiring_notified_1d_at = None
        clinic.save(
            update_fields=[
                "current_period_end", "expiring_notified_30d_at", "expiring_notified_15d_at",
                "expiring_notified_7d_at", "expiring_notified_1d_at",
            ]
        )

    # Changement de formule fait côté Stripe (portail client) ou confirmation de change_subscribed_plan :
    # la formule de la clinique suit le Price de l'abonnement.
    items = (payload.get("items") or {}).get("data") or []
    price_id = ((items[0].get("price") or {}).get("id") if items else "") or ""
    plan = plan_for_stripe_price_id(price_id)
    if plan and plan != (clinic.plan_tier, clinic.billing_cycle):
        change_plan(
            clinic=clinic, plan_tier=plan[0], billing_cycle=plan[1], changed_by=None,
            metadata={"source": SubscriptionEvent.Source.STRIPE_WEBHOOK, "stripe_event_id": event_id},
        )

    new_status = _STRIPE_STATUS_MAP.get(payload.get("status"))
    if new_status and new_status != clinic.subscription_status:
        change_subscription_status(
            clinic=clinic, status=new_status, changed_by=None,
            source=SubscriptionEvent.Source.STRIPE_WEBHOOK, stripe_event_id=event_id,
            metadata={"clinic_id": clinic.pk, "stripe_status": payload.get("status")},
        )


def _on_subscription_deleted(*, event_id, payload):
    clinic = _resolve_clinic(payload)
    if not clinic:
        return
    change_subscription_status(
        clinic=clinic, status=Clinic.SubscriptionStatus.CANCELLED, changed_by=None,
        source=SubscriptionEvent.Source.STRIPE_WEBHOOK, stripe_event_id=event_id,
        metadata={"clinic_id": clinic.pk},
    )


def _on_invoice_payment_failed(*, event_id, payload):
    clinic = _resolve_clinic(payload)
    if not clinic:
        return
    change_subscription_status(
        clinic=clinic, status=Clinic.SubscriptionStatus.PAST_DUE, changed_by=None,
        source=SubscriptionEvent.Source.STRIPE_WEBHOOK, stripe_event_id=event_id,
        metadata={"clinic_id": clinic.pk},
    )


_EVENT_HANDLERS = {
    "checkout.session.completed": _on_checkout_completed,
    "customer.subscription.updated": _on_subscription_updated,
    "customer.subscription.deleted": _on_subscription_deleted,
    "invoice.payment_failed": _on_invoice_payment_failed,
}


def handle_stripe_event(*, event_type: str, event_id: str, payload: dict) -> None:
    """Distribue un événement Stripe vérifié vers la bonne transition. Appelée uniquement depuis
    api/views.py::StripeWebhookView après que construct_webhook_event() a vérifié la signature —
    jamais appelée avec des données non vérifiées. Ne fait rien (ce n'est pas une erreur) pour un
    type d'événement non reconnu ou une clinique non résolvable — un webhook ne doit jamais renvoyer
    un 500 pour un événement qui ne nous concerne pas."""
    if event_id and SubscriptionEvent.objects.filter(stripe_event_id=event_id).exists():
        return  # déjà traité — Stripe peut redélivrer le même événement

    handler = _EVENT_HANDLERS.get(event_type)
    if handler:
        handler(event_id=event_id, payload=payload)


def _notify_license_expired(*, clinic: Clinic, trial_ended: bool = False) -> None:
    from django.utils import translation

    from communication.models import NotificationLog
    from communication.services import PLATFORM_SIGNATURE, compose_email, language_for_user, send_notification

    def build():
        values = {"clinic": clinic.name}
        if trial_ended:
            subject = _("Fin de votre mois d'essai gratuit — %(clinic)s") % values
            paragraphs = [
                _("Le mois d'essai gratuit de %(clinic)s est arrivé à son terme. Nous espérons que la "
                  "plateforme a répondu à vos attentes.") % values,
                _("La création de nouveaux enregistrements est suspendue jusqu'à la souscription d'une "
                  "formule. Vos données restent intégralement conservées et consultables."),
                _("Pour réactiver votre compte, choisissez une formule depuis l'écran « Abonnement »."),
            ]
        else:
            subject = _("Suspension de votre abonnement — %(clinic)s") % values
            paragraphs = [
                _("Nous vous informons que l'abonnement de %(clinic)s est suspendu.") % values,
                _("La création de nouveaux enregistrements est bloquée jusqu'à sa réactivation. Vos données "
                  "restent intégralement conservées et consultables."),
                _("Pour régulariser votre situation, rendez-vous sur l'écran « Abonnement »."),
            ]
        return subject, compose_email(paragraphs=paragraphs, signature=PLATFORM_SIGNATURE)

    for admin_user in clinic.users.filter(groups__name="clinic_admin"):
        if admin_user.email:
            # Langue de chaque administrateur (docs/i18n.md §2).
            with translation.override(language_for_user(admin_user)):
                subject, body = build()
            send_notification(
                clinic=clinic, recipient_user=admin_user, channel=NotificationLog.Channel.EMAIL,
                notification_type=NotificationLog.NotificationType.LICENSE_EXPIRED,
                recipient_address=admin_user.email, subject=subject, body=body,
            )
        # docs/known-issues.md : User n'a pas de champ phone — le canal SMS est aujourd'hui
        # inaccessible pour les destinataires clinic_admin, même manque préexistant que pour les
        # notifications de rendez-vous.


def expire_trials(*, now=None) -> int:
    """Suspend les cliniques dont l'essai gratuit est terminé sans souscription (décision métier du
    2026-09-28). Appelée chaque jour par tasks.py::expire_ended_trials. Une clinique qui a souscrit
    pendant l'essai est déjà Active (voir _STRIPE_STATUS_MAP) et n'est donc jamais concernée. Les
    données restent consultables (SubscriptionActivePermission ne bloque que l'écriture)."""
    from django.utils import timezone

    now = now or timezone.now()
    expired = Clinic.objects.filter(subscription_status=Clinic.SubscriptionStatus.TRIAL, trial_ends_at__lte=now)
    count = 0
    for clinic in expired:
        change_subscription_status(
            clinic=clinic, status=Clinic.SubscriptionStatus.SUSPENDED, changed_by=None,
            source=SubscriptionEvent.Source.SYSTEM, metadata={"clinic_id": clinic.pk, "reason": "trial_ended"},
        )
        count += 1
    return count
