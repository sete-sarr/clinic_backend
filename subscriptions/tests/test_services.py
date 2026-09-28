from django.test import TestCase

from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog
from common.testing import create_clinic, create_user
from subscriptions.models import SubscriptionEvent
from subscriptions.services import (
    change_plan,
    change_subscription_status,
    handle_stripe_event,
)


class ChangeSubscriptionStatusTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()  # par défaut TRIAL/STARTER/MONTHLY

    def test_allowed_transition_updates_clinic_and_creates_event(self):
        change_subscription_status(
            clinic=self.clinic, status=Clinic.SubscriptionStatus.ACTIVE, changed_by=None,
            source=SubscriptionEvent.Source.SYSTEM,
        )
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.ACTIVE)
        event = SubscriptionEvent.objects.get(clinic=self.clinic)
        self.assertEqual(event.from_status, Clinic.SubscriptionStatus.TRIAL)
        self.assertEqual(event.to_status, Clinic.SubscriptionStatus.ACTIVE)

    def test_illegal_transition_raises_value_error(self):
        # Trial -> Past Due reste interdite (Trial -> Suspended est autorisée depuis le 2026-09-28 :
        # fin d'essai sans souscription, voir expire_trials).
        with self.assertRaises(ValueError):
            change_subscription_status(
                clinic=self.clinic, status=Clinic.SubscriptionStatus.PAST_DUE, changed_by=None,
                source=SubscriptionEvent.Source.SYSTEM,
            )
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.TRIAL)

    def test_same_status_is_a_noop_transition_allowed(self):
        change_subscription_status(
            clinic=self.clinic, status=Clinic.SubscriptionStatus.TRIAL, changed_by=None,
            source=SubscriptionEvent.Source.SYSTEM,
        )
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.TRIAL)

    def test_writes_audit_log_with_clinic_id_in_metadata(self):
        admin_user = create_user(clinic=None, role=None, username="platform_admin", is_superuser=True)
        change_subscription_status(
            clinic=self.clinic, status=Clinic.SubscriptionStatus.ACTIVE, changed_by=admin_user,
            source=SubscriptionEvent.Source.ADMIN_MANUAL, metadata={"note": "manual test"},
        )
        log = AuditLog.objects.filter(model_name="Clinic", object_id=str(self.clinic.pk)).latest("created_at")
        self.assertEqual(log.metadata["clinic_id"], self.clinic.pk)
        self.assertEqual(log.metadata["from_status"], Clinic.SubscriptionStatus.TRIAL)
        self.assertEqual(log.metadata["to_status"], Clinic.SubscriptionStatus.ACTIVE)
        self.assertEqual(log.metadata["note"], "manual test")

    def test_pins_known_audit_log_clinic_is_none_gap(self):
        """Documente docs/known-issues.md : le clinic = getattr(obj, "clinic", None) or
        getattr(user, "clinic", None) de record_audit se résout à None pour un objet Clinic audité
        par un superutilisateur (dont le .clinic est lui-même None) — atténué via metadata.clinic_id
        ci-dessus, pas corrigé ici."""
        admin_user = create_user(clinic=None, role=None, username="platform_admin2", is_superuser=True)
        change_subscription_status(
            clinic=self.clinic, status=Clinic.SubscriptionStatus.ACTIVE, changed_by=admin_user,
            source=SubscriptionEvent.Source.ADMIN_MANUAL,
        )
        log = AuditLog.objects.filter(model_name="Clinic", object_id=str(self.clinic.pk)).latest("created_at")
        self.assertIsNone(log.clinic)

    def test_suspended_transition_triggers_license_expired_notification(self):
        from communication.models import NotificationLog

        clinic_admin = create_user(clinic=self.clinic, role="clinic_admin", email="admin@example.com")
        change_subscription_status(
            clinic=self.clinic, status=Clinic.SubscriptionStatus.ACTIVE, changed_by=None,
            source=SubscriptionEvent.Source.SYSTEM,
        )
        change_subscription_status(
            clinic=self.clinic, status=Clinic.SubscriptionStatus.PAST_DUE, changed_by=None,
            source=SubscriptionEvent.Source.SYSTEM,
        )
        change_subscription_status(
            clinic=self.clinic, status=Clinic.SubscriptionStatus.SUSPENDED, changed_by=None,
            source=SubscriptionEvent.Source.SYSTEM,
        )
        self.assertTrue(
            NotificationLog.objects.filter(
                notification_type=NotificationLog.NotificationType.LICENSE_EXPIRED,
                recipient_address="admin@example.com",
            ).exists()
        )


class ChangePlanTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()

    def test_updates_tier_and_cycle_and_audits(self):
        change_plan(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.PROFESSIONAL,
            billing_cycle=Clinic.BillingCycle.ANNUAL, changed_by=None,
        )
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.plan_tier, Clinic.PlanTier.PROFESSIONAL)
        self.assertEqual(self.clinic.billing_cycle, Clinic.BillingCycle.ANNUAL)
        log = AuditLog.objects.filter(model_name="Clinic", object_id=str(self.clinic.pk)).latest("created_at")
        self.assertEqual(log.metadata["to_plan"], Clinic.PlanTier.PROFESSIONAL)


class HandleStripeEventTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()

    def test_checkout_completed_activates_clinic_and_sets_customer_id(self):
        handle_stripe_event(
            event_type="checkout.session.completed", event_id="evt_1",
            payload={"metadata": {"clinic_id": str(self.clinic.pk)}, "customer": "cus_123", "subscription": "sub_123"},
        )
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.ACTIVE)
        self.assertEqual(self.clinic.stripe_customer_id, "cus_123")
        self.assertEqual(self.clinic.stripe_subscription_id, "sub_123")

    def test_subscription_updated_maps_past_due_and_resets_reminders(self):
        from django.utils import timezone

        self.clinic.stripe_customer_id = "cus_456"
        self.clinic.subscription_status = Clinic.SubscriptionStatus.ACTIVE
        self.clinic.expiring_notified_30d_at = timezone.now()
        self.clinic.save()
        handle_stripe_event(
            event_type="customer.subscription.updated", event_id="evt_2",
            payload={"customer": "cus_456", "status": "past_due", "current_period_end": 1893456000},  # 2030-01-01
        )
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.PAST_DUE)
        self.assertIsNone(self.clinic.expiring_notified_30d_at)

    def test_subscription_deleted_cancels_clinic(self):
        self.clinic.stripe_customer_id = "cus_789"
        self.clinic.subscription_status = Clinic.SubscriptionStatus.ACTIVE
        self.clinic.save()
        handle_stripe_event(
            event_type="customer.subscription.deleted", event_id="evt_3", payload={"customer": "cus_789"}
        )
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.CANCELLED)

    def test_invoice_payment_failed_sets_past_due(self):
        self.clinic.stripe_customer_id = "cus_999"
        self.clinic.subscription_status = Clinic.SubscriptionStatus.ACTIVE
        self.clinic.save()
        handle_stripe_event(
            event_type="invoice.payment_failed", event_id="evt_4", payload={"customer": "cus_999"}
        )
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.PAST_DUE)

    def test_duplicate_event_id_is_a_noop(self):
        payload = {"metadata": {"clinic_id": str(self.clinic.pk)}, "customer": "cus_dup"}
        handle_stripe_event(event_type="checkout.session.completed", event_id="evt_dup", payload=payload)
        count_after_first = SubscriptionEvent.objects.filter(clinic=self.clinic).count()
        handle_stripe_event(event_type="checkout.session.completed", event_id="evt_dup", payload=payload)
        self.assertEqual(SubscriptionEvent.objects.filter(clinic=self.clinic).count(), count_after_first)

    def test_unresolvable_clinic_is_a_noop_not_an_error(self):
        handle_stripe_event(
            event_type="checkout.session.completed", event_id="evt_5",
            payload={"metadata": {}, "customer": "cus_unknown"},
        )  # ne doit pas lever d'exception

    def test_unknown_event_type_is_a_noop(self):
        handle_stripe_event(event_type="some.unrelated.event", event_id="evt_6", payload={})  # ne doit pas lever d'exception


class TrialAndPricingRulesTests(TestCase):
    """Décision métier du 2026-09-28 : premier mois gratuit, suspension à la fin de l'essai sans
    souscription, seules Starter/Professional sont proposées à la souscription."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        self.now = timezone.now()
        self.timedelta = timedelta
        self.clinic = create_clinic()

    def _set_trial_end(self, clinic, days):
        clinic.subscription_status = Clinic.SubscriptionStatus.TRIAL
        clinic.trial_ends_at = self.now + self.timedelta(days=days)
        clinic.save(update_fields=["subscription_status", "trial_ends_at"])

    def test_start_trial_gives_thirty_days(self):
        from subscriptions.services import start_trial

        start_trial(clinic=self.clinic)
        self.clinic.refresh_from_db()
        remaining = self.clinic.trial_ends_at - self.now
        self.assertEqual(round(remaining.total_seconds() / 86400), 30)

    def test_expire_trials_suspends_only_ended_trials(self):
        from subscriptions.services import expire_trials

        ended = self.clinic
        self._set_trial_end(ended, days=-1)
        running = create_clinic("Running Trial")
        self._set_trial_end(running, days=10)
        active = create_clinic("Paying Clinic")
        active.subscription_status = Clinic.SubscriptionStatus.ACTIVE
        active.trial_ends_at = self.now - self.timedelta(days=5)
        active.save(update_fields=["subscription_status", "trial_ends_at"])

        self.assertEqual(expire_trials(), 1)

        ended.refresh_from_db()
        running.refresh_from_db()
        active.refresh_from_db()
        self.assertEqual(ended.subscription_status, Clinic.SubscriptionStatus.SUSPENDED)
        self.assertEqual(running.subscription_status, Clinic.SubscriptionStatus.TRIAL)
        self.assertEqual(active.subscription_status, Clinic.SubscriptionStatus.ACTIVE)
        event = SubscriptionEvent.objects.get(clinic=ended, to_status=Clinic.SubscriptionStatus.SUSPENDED)
        self.assertEqual(event.metadata["reason"], "trial_ended")

    def test_expire_trials_is_idempotent(self):
        from subscriptions.services import expire_trials

        self._set_trial_end(self.clinic, days=-1)
        expire_trials()
        self.assertEqual(expire_trials(), 0)

    def test_checkout_rejects_enterprise_and_invalid_cycle(self):
        from subscriptions.services import start_checkout

        result = start_checkout(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.ENTERPRISE, billing_cycle=Clinic.BillingCycle.MONTHLY,
            success_url="https://x/ok", cancel_url="https://x/ko",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "Cette formule n'est pas disponible à la souscription.")

        result = start_checkout(
            clinic=self.clinic, plan_tier=Clinic.PlanTier.STARTER, billing_cycle="weekly",
            success_url="https://x/ok", cancel_url="https://x/ko",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "Cycle de facturation invalide.")

    def test_stripe_trialing_subscription_marks_clinic_active(self):
        self.clinic.stripe_customer_id = "cus_trial"
        self.clinic.save(update_fields=["stripe_customer_id"])
        handle_stripe_event(
            event_type="customer.subscription.updated", event_id="evt_trialing",
            payload={"customer": "cus_trial", "status": "trialing"},
        )
        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.ACTIVE)


class CheckoutCompletedRecordsPlanTests(TestCase):
    def test_subscribed_plan_and_cycle_are_saved_on_the_clinic(self):
        clinic = create_clinic()  # TRIAL / STARTER / MONTHLY par défaut
        handle_stripe_event(
            event_type="checkout.session.completed", event_id="evt_checkout_pro",
            payload={
                "customer": "cus_pro", "subscription": "sub_pro",
                "metadata": {"clinic_id": str(clinic.pk), "plan_tier": "professional", "billing_cycle": "annual"},
            },
        )
        clinic.refresh_from_db()
        self.assertEqual(clinic.subscription_status, Clinic.SubscriptionStatus.ACTIVE)
        self.assertEqual(clinic.plan_tier, Clinic.PlanTier.PROFESSIONAL)
        self.assertEqual(clinic.billing_cycle, Clinic.BillingCycle.ANNUAL)


class SubscriptionUpdatedSyncsPlanTests(TestCase):
    def test_price_change_made_in_stripe_portal_updates_clinic_plan(self):
        from django.test import override_settings

        clinic = create_clinic()
        clinic.subscription_status = Clinic.SubscriptionStatus.ACTIVE
        clinic.stripe_customer_id = "cus_portal"
        clinic.save(update_fields=["subscription_status", "stripe_customer_id"])
        with override_settings(STRIPE_PRICE_PROFESSIONAL_MONTHLY="price_pro_monthly"):
            handle_stripe_event(
                event_type="customer.subscription.updated", event_id="evt_portal_change",
                payload={
                    "customer": "cus_portal", "status": "active",
                    "items": {"data": [{"id": "si_1", "price": {"id": "price_pro_monthly"}}]},
                },
            )
        clinic.refresh_from_db()
        self.assertEqual(clinic.plan_tier, Clinic.PlanTier.PROFESSIONAL)
        self.assertEqual(clinic.subscription_status, Clinic.SubscriptionStatus.ACTIVE)
