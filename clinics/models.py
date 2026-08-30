from django.db import models

from common.models import TimeStampedModel


def clinic_logo_upload_path(instance, filename):
    # Tenant-scoped path (CLAUDE.md multi-tenant isolation) — avoids filename collisions across
    # clinics. instance.pk always exists here: Clinic rows are provisioned once at tenant
    # onboarding, this upload_to is only ever hit via a PATCH on an already-existing clinic.
    return f"clinic_logos/{instance.pk}/{filename}"


class Clinic(TimeStampedModel):
    class SubscriptionStatus(models.TextChoices):
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"

    class PlanTier(models.TextChoices):
        STARTER = "starter", "Starter"
        PROFESSIONAL = "professional", "Professional"
        ENTERPRISE = "enterprise", "Enterprise"

    class BillingCycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        ANNUAL = "annual", "Annual"

    class Locale(models.TextChoices):
        FRENCH = "fr", "Français"
        ENGLISH = "en", "English"

    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    # Branding / preferences (design-system/branding.md). locale only stores the preference for
    # now — no runtime translation exists yet, the UI stays French regardless of this value.
    locale = models.CharField(max_length=2, choices=Locale.choices, default=Locale.FRENCH)
    logo_light = models.ImageField(upload_to=clinic_logo_upload_path, blank=True, null=True)
    logo_dark = models.ImageField(upload_to=clinic_logo_upload_path, blank=True, null=True)
    logo_print = models.ImageField(upload_to=clinic_logo_upload_path, blank=True, null=True)
    favicon = models.ImageField(upload_to=clinic_logo_upload_path, blank=True, null=True)

    # Platform subscription billing (business/subscription-billing-policy.md) — the clinic paying
    # for its own use of the platform, entirely separate from backend/billing/ (clinic billing its
    # patients). No dollar amounts stored here: plan_tier/billing_cycle only key into the Stripe
    # Price ID catalog (subscriptions/catalog.py); Stripe's own Price object is the source of truth
    # for the actual charge amount.
    subscription_status = models.CharField(
        max_length=16, choices=SubscriptionStatus.choices, default=SubscriptionStatus.TRIAL
    )
    plan_tier = models.CharField(max_length=16, choices=PlanTier.choices, default=PlanTier.STARTER)
    billing_cycle = models.CharField(max_length=8, choices=BillingCycle.choices, default=BillingCycle.MONTHLY)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    # SUBSCRIPTION EXPIRING notification idempotency (business/notification-rules.md's 4-point
    # schedule) — reset to null whenever current_period_end advances (renewal), so the next cycle's
    # reminders fire again instead of staying permanently "already notified".
    expiring_notified_30d_at = models.DateTimeField(null=True, blank=True)
    expiring_notified_15d_at = models.DateTimeField(null=True, blank=True)
    expiring_notified_7d_at = models.DateTimeField(null=True, blank=True)
    expiring_notified_1d_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
