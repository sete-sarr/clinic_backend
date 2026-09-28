"""Tarification de la plateforme — décision métier du 2026-09-28 : francs CFA (XOF), deux formules
proposées à la souscription (Starter 10 000 / mois, Professional 15 000 / mois), cycle annuel à
x10 (2 mois offerts), premier mois gratuit (TRIAL_DAYS). Enterprise reste une valeur de
Clinic.PlanTier (aucune migration, cliniques existantes éventuelles inchangées) mais n'est plus
proposé à la souscription (SUBSCRIBABLE_TIERS).

Le montant réellement prélevé provient toujours du Stripe Price référencé par
STRIPE_PRICE_<TIER>_<CYCLE> (créé en XOF dans le tableau de bord Stripe) — jamais calculé ni vérifié
à partir de monthly_price_xof, qui sert uniquement à l'affichage et doit rester aligné sur ces Prices."""

from django.conf import settings
from django.db.models import Q

from clinics.models import Clinic

CURRENCY = "XOF"
TRIAL_DAYS = 30
ANNUAL_MONTHS_BILLED = 10  # 2 mois offerts sur l'annuel

PLAN_LIMITS = {
    Clinic.PlanTier.STARTER: {"max_doctors": 3, "max_patients": 500, "monthly_price_xof": 10_000},
    Clinic.PlanTier.PROFESSIONAL: {"max_doctors": 10, "max_patients": 5000, "monthly_price_xof": 15_000},
    Clinic.PlanTier.ENTERPRISE: {"max_doctors": None, "max_patients": None, "monthly_price_xof": None},
}

SUBSCRIBABLE_TIERS = (Clinic.PlanTier.STARTER, Clinic.PlanTier.PROFESSIONAL)

# Fonctionnalités réservées par formule (décision métier du 2026-09-28) : les SMS de rendez-vous et
# le rappel groupé de la veille sont réservés à Professional (15 000 FCFA). Starter garde les
# e-mails individuels de rendez-vous (création, modification, annulation). L'essai gratuit donne
# accès aux fonctionnalités de Professional.
APPOINTMENT_SMS = "appointment_sms"
APPOINTMENT_REMINDERS = "appointment_reminders"

_PROFESSIONAL_FEATURES = frozenset({APPOINTMENT_SMS, APPOINTMENT_REMINDERS})
PLAN_FEATURES = {
    Clinic.PlanTier.STARTER: frozenset(),
    Clinic.PlanTier.PROFESSIONAL: _PROFESSIONAL_FEATURES,
    Clinic.PlanTier.ENTERPRISE: _PROFESSIONAL_FEATURES,
}
TRIAL_FEATURES = _PROFESSIONAL_FEATURES


def has_feature(clinic, feature: str) -> bool:
    if clinic.subscription_status == Clinic.SubscriptionStatus.TRIAL:
        return feature in TRIAL_FEATURES
    return feature in PLAN_FEATURES.get(clinic.plan_tier, frozenset())


def clinics_with_feature_q(feature: str, *, prefix: str = "") -> Q:
    """Même règle que has_feature, en filtre de queryset (prefix="clinic__" depuis un modèle lié
    à Clinic) — pour les traitements par lot comme les rappels de la veille."""
    tiers = [tier for tier, features in PLAN_FEATURES.items() if feature in features]
    q = Q(**{f"{prefix}plan_tier__in": tiers}) & ~Q(**{f"{prefix}subscription_status": Clinic.SubscriptionStatus.TRIAL})
    if feature in TRIAL_FEATURES:
        q |= Q(**{f"{prefix}subscription_status": Clinic.SubscriptionStatus.TRIAL})
    return q


def _stripe_price_ids():
    return {
        (Clinic.PlanTier.STARTER, Clinic.BillingCycle.MONTHLY): settings.STRIPE_PRICE_STARTER_MONTHLY,
        (Clinic.PlanTier.STARTER, Clinic.BillingCycle.ANNUAL): settings.STRIPE_PRICE_STARTER_ANNUAL,
        (Clinic.PlanTier.PROFESSIONAL, Clinic.BillingCycle.MONTHLY): settings.STRIPE_PRICE_PROFESSIONAL_MONTHLY,
        (Clinic.PlanTier.PROFESSIONAL, Clinic.BillingCycle.ANNUAL): settings.STRIPE_PRICE_PROFESSIONAL_ANNUAL,
        (Clinic.PlanTier.ENTERPRISE, Clinic.BillingCycle.MONTHLY): settings.STRIPE_PRICE_ENTERPRISE_MONTHLY,
        (Clinic.PlanTier.ENTERPRISE, Clinic.BillingCycle.ANNUAL): settings.STRIPE_PRICE_ENTERPRISE_ANNUAL,
    }


def plan_for_stripe_price_id(price_id: str):
    """(plan_tier, billing_cycle) correspondant à un Stripe Price, ou None — utilisé par le webhook
    pour refléter un changement de formule fait côté Stripe (portail client)."""
    if not price_id:
        return None
    for key, configured in _stripe_price_ids().items():
        if configured == price_id:
            return key
    return None


def get_stripe_price_id(*, plan_tier: str, billing_cycle: str) -> str:
    price_id = _stripe_price_ids().get((plan_tier, billing_cycle), "")
    if not price_id:
        raise ValueError(f"No Stripe Price ID configured for {plan_tier}/{billing_cycle}.")
    return price_id
