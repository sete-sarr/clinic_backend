from django.db import transaction

from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog

from .models import SubscriptionEvent

# Settled lifecycle (business/subscription-billing-policy.md): Trial -> Active -> Past Due ->
# Suspended -> Cancelled, with Trial/PastDue/Suspended each able to short-circuit to Cancelled.
_ALLOWED_TRANSITIONS = {
    Clinic.SubscriptionStatus.TRIAL: {Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.CANCELLED},
    Clinic.SubscriptionStatus.ACTIVE: {Clinic.SubscriptionStatus.PAST_DUE, Clinic.SubscriptionStatus.CANCELLED},
    Clinic.SubscriptionStatus.PAST_DUE: {Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.SUSPENDED},
    Clinic.SubscriptionStatus.SUSPENDED: {Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.CANCELLED},
    Clinic.SubscriptionStatus.CANCELLED: set(),
}


def start_trial(*, clinic: Clinic, trial_days: int = 14) -> Clinic:
    """Hook point for wherever clinic provisioning happens (tenant provisioning itself doesn't
    exist yet in this codebase — out of scope here)."""
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
    """The ONLY function that may write Clinic.subscription_status. Validates the transition,
    persists it, writes a SubscriptionEvent row, and calls record_audit with clinic_id explicitly
    in metadata (docs/known-issues.md: record_audit resolves AuditLog.clinic via obj.clinic or
    user.clinic, both None/wrong for a superuser- or webhook-driven Clinic audit — this is a
    documented mitigation, not a fix to the shared helper)."""
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
        _notify_license_expired(clinic=clinic)

    return clinic


def change_plan(*, clinic: Clinic, plan_tier: str, billing_cycle: str, changed_by, metadata: dict | None = None) -> Clinic:
    """Plan/cycle changes independent of status changes (e.g. upgrade while Active)."""
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
    """Thin pass-through to the resolved PaymentProvider — the only place api/views.py may call
    into for creating a Checkout Session."""
    from .providers import get_payment_provider

    return get_payment_provider().create_checkout_session(
        clinic=clinic, plan_tier=plan_tier, billing_cycle=billing_cycle,
        success_url=success_url, cancel_url=cancel_url,
    )


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
    change_subscription_status(
        clinic=clinic, status=Clinic.SubscriptionStatus.ACTIVE, changed_by=None,
        source=SubscriptionEvent.Source.STRIPE_WEBHOOK, stripe_event_id=event_id,
        metadata={"clinic_id": clinic.pk},
    )


_STRIPE_STATUS_MAP = {
    "active": Clinic.SubscriptionStatus.ACTIVE,
    "trialing": Clinic.SubscriptionStatus.TRIAL,
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
        # Renewal — reset the 4-point expiry-notification idempotency guard so next cycle's
        # reminders fire again instead of staying permanently "already notified".
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
    """Dispatches a verified Stripe event to the right transition. Called only from
    api/views.py::StripeWebhookView after construct_webhook_event() has verified the signature —
    never called with unverified data. A no-op (not an error) for unrecognized event types or an
    unresolvable clinic — webhooks must never 500 on an event we don't care about."""
    if event_id and SubscriptionEvent.objects.filter(stripe_event_id=event_id).exists():
        return  # already processed — Stripe may redeliver the same event

    handler = _EVENT_HANDLERS.get(event_type)
    if handler:
        handler(event_id=event_id, payload=payload)


def _notify_license_expired(*, clinic: Clinic) -> None:
    from communication.models import NotificationLog
    from communication.services import send_notification

    for admin_user in clinic.users.filter(groups__name="clinic_admin"):
        if admin_user.email:
            send_notification(
                clinic=clinic, recipient_user=admin_user, channel=NotificationLog.Channel.EMAIL,
                notification_type=NotificationLog.NotificationType.LICENSE_EXPIRED,
                recipient_address=admin_user.email,
                subject=f"Your {clinic.name} subscription has been suspended",
                body=(
                    f"{clinic.name}'s platform subscription has been suspended. New records can no "
                    f"longer be created until the subscription is reactivated. Existing data remains "
                    f"accessible."
                ),
            )
        # docs/known-issues.md: User has no phone field — SMS channel unreachable for
        # clinic_admin recipients today, same pre-existing gap as appointment notifications.
