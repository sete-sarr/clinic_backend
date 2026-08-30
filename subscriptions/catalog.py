"""PLACEHOLDER pricing/limits — framework (USD/monthly+annual/Stripe/3-tier) is a settled decision
(2026-08-12), but these numbers are indicative only, pending real business sign-off. Edit env vars
to change $ figures; edit PLAN_LIMITS to change feature-gate limits. Neither needs a migration.
Actual charge amounts always come from the Stripe Price referenced by STRIPE_PRICE_<TIER>_<CYCLE> —
never computed or verified from indicative_monthly_usd, which is UI-display-only."""

from django.conf import settings

from clinics.models import Clinic

PLAN_LIMITS = {
    Clinic.PlanTier.STARTER: {"max_doctors": 3, "max_patients": 500, "indicative_monthly_usd": 29},
    Clinic.PlanTier.PROFESSIONAL: {"max_doctors": 10, "max_patients": 5000, "indicative_monthly_usd": 79},
    Clinic.PlanTier.ENTERPRISE: {"max_doctors": None, "max_patients": None, "indicative_monthly_usd": 199},
}


def _stripe_price_ids():
    return {
        (Clinic.PlanTier.STARTER, Clinic.BillingCycle.MONTHLY): settings.STRIPE_PRICE_STARTER_MONTHLY,
        (Clinic.PlanTier.STARTER, Clinic.BillingCycle.ANNUAL): settings.STRIPE_PRICE_STARTER_ANNUAL,
        (Clinic.PlanTier.PROFESSIONAL, Clinic.BillingCycle.MONTHLY): settings.STRIPE_PRICE_PROFESSIONAL_MONTHLY,
        (Clinic.PlanTier.PROFESSIONAL, Clinic.BillingCycle.ANNUAL): settings.STRIPE_PRICE_PROFESSIONAL_ANNUAL,
        (Clinic.PlanTier.ENTERPRISE, Clinic.BillingCycle.MONTHLY): settings.STRIPE_PRICE_ENTERPRISE_MONTHLY,
        (Clinic.PlanTier.ENTERPRISE, Clinic.BillingCycle.ANNUAL): settings.STRIPE_PRICE_ENTERPRISE_ANNUAL,
    }


def get_stripe_price_id(*, plan_tier: str, billing_cycle: str) -> str:
    price_id = _stripe_price_ids().get((plan_tier, billing_cycle), "")
    if not price_id:
        raise ValueError(f"No Stripe Price ID configured for {plan_tier}/{billing_cycle}.")
    return price_id
