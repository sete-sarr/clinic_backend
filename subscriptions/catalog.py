"""Tarification de la plateforme — décision métier du 2026-09-28 : francs CFA (XOF), deux formules
proposées à la souscription (Starter 10 000 / mois, Professional 15 000 / mois), cycle annuel à
x9 (3 mois offerts, décision du 2026-10-14 ; x10 auparavant). Essai gratuit de 14 jours (TRIAL_DAYS, décision du 2026-10-14 ; 30 jours
auparavant — les essais en cours gardent leur date de fin). Enterprise reste une valeur de
Clinic.PlanTier (aucune migration, cliniques existantes éventuelles inchangées) mais n'est plus
proposé à la souscription (SUBSCRIBABLE_TIERS).

Le montant réellement prélevé provient toujours du Stripe Price référencé par
STRIPE_PRICE_<TIER>_<CYCLE> (créé en XOF dans le tableau de bord Stripe) — jamais calculé ni vérifié
à partir de monthly_price_xof, qui sert uniquement à l'affichage et doit rester aligné sur ces Prices."""

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from clinics.models import Clinic

CURRENCY = "XOF"
TRIAL_DAYS = 14
ANNUAL_MONTHS_BILLED = 9  # 3 mois offerts sur l'annuel
# Abonnement prépayé (SasPay) : durée d'une période et délai de grâce après l'échéance pendant lequel
# la clinique peut encore tout faire (Paiement en retard), avant la lecture seule (décision du 2026-10-14).
PERIOD_MONTHS = {Clinic.BillingCycle.MONTHLY: 1, Clinic.BillingCycle.ANNUAL: 12}
GRACE_DAYS = 3

PLAN_LIMITS = {
    Clinic.PlanTier.STARTER: {"max_doctors": 3, "max_patients": 500, "monthly_price_xof": 10_000},
    Clinic.PlanTier.PROFESSIONAL: {"max_doctors": 10, "max_patients": 5000, "monthly_price_xof": 15_000},
    Clinic.PlanTier.ENTERPRISE: {"max_doctors": None, "max_patients": None, "monthly_price_xof": None},
}

SUBSCRIBABLE_TIERS = (Clinic.PlanTier.STARTER, Clinic.PlanTier.PROFESSIONAL)

# Fonctionnalités réservées à Professional (15 000 FCFA ; décisions du 2026-09-28 et du 2026-10-14) :
# modules hospitalisation, laboratoire et pharmacie, rappel de la veille, SMS (désactivés tant que
# SMS_ENABLED est faux). Starter : patients, rendez-vous et e-mails de rendez-vous, consultations,
# ordonnances, facturation. L'essai gratuit donne accès à tout Professional.
APPOINTMENT_SMS = "appointment_sms"
APPOINTMENT_REMINDERS = "appointment_reminders"
HOSPITALIZATION = "hospitalization"
LABORATORY = "laboratory"
PHARMACY = "pharmacy"
# Modules (écrans) dont l'accès dépend de la formule — exposés au frontend (menu).
PLAN_MODULES = (HOSPITALIZATION, LABORATORY, PHARMACY)

_PROFESSIONAL_FEATURES = frozenset({APPOINTMENT_SMS, APPOINTMENT_REMINDERS, *PLAN_MODULES})
PLAN_FEATURES = {
    Clinic.PlanTier.STARTER: frozenset(),
    Clinic.PlanTier.PROFESSIONAL: _PROFESSIONAL_FEATURES,
    Clinic.PlanTier.ENTERPRISE: _PROFESSIONAL_FEATURES,
}
TRIAL_FEATURES = _PROFESSIONAL_FEATURES


def plan_price_xof(plan_tier: str, billing_cycle: str, clinic=None) -> int:
    """Prix d'une période, hors frais de paiement (ajoutés par SasPay et payés par la clinique). Une
    clinique désignée pour un essai réel (SUBSCRIPTION_TEST_CLINIC_IDS) paie le prix d'essai."""
    if (
        clinic is not None
        and settings.SUBSCRIPTION_TEST_PRICE_XOF
        and clinic.pk in settings.SUBSCRIPTION_TEST_CLINIC_IDS
    ):
        return settings.SUBSCRIPTION_TEST_PRICE_XOF
    monthly = PLAN_LIMITS[plan_tier]["monthly_price_xof"]
    return monthly * ANNUAL_MONTHS_BILLED if billing_cycle == Clinic.BillingCycle.ANNUAL else monthly


def prepaid_lapsed(clinic, now=None) -> bool:
    """Abonnement prépayé non renouvelé : vrai une fois l'échéance et le délai de grâce passés, sans
    attendre la tâche quotidienne qui passe la clinique en Suspendu (prepaid.expire_prepaid_periods).
    Un abonnement Stripe (prélèvement automatique) est suivi par ses webhooks, jamais ici."""
    from datetime import timedelta

    return (
        clinic.subscription_status in (Clinic.SubscriptionStatus.ACTIVE, Clinic.SubscriptionStatus.PAST_DUE)
        and not clinic.stripe_subscription_id
        and not clinic.is_demo
        and clinic.current_period_end is not None
        and clinic.current_period_end + timedelta(days=GRACE_DAYS) <= (now or timezone.now())
    )


def writes_blocked(clinic, now=None) -> bool:
    """Lecture seule effective : essai terminé ou abonnement prépayé échu (délai de grâce compris)."""
    return trial_expired(clinic, now) or prepaid_lapsed(clinic, now)


def trial_expired(clinic, now=None) -> bool:
    """Essai terminé sans souscription : vrai dès la date de fin, sans attendre la tâche quotidienne
    qui passe la clinique en Suspendu (subscriptions/services.py::expire_trials). Jamais pour la
    clinique de démonstration."""
    return (
        clinic.subscription_status == Clinic.SubscriptionStatus.TRIAL
        and not clinic.is_demo
        and clinic.trial_ends_at is not None
        and clinic.trial_ends_at <= (now or timezone.now())
    )


def has_feature(clinic, feature: str) -> bool:
    if feature == APPOINTMENT_SMS and not settings.SMS_ENABLED:
        return False
    if clinic.subscription_status == Clinic.SubscriptionStatus.TRIAL:
        return feature in TRIAL_FEATURES
    return feature in PLAN_FEATURES.get(clinic.plan_tier, frozenset())


def visible_modules(clinic) -> list[str]:
    """Modules affichés dans le menu. Une clinique abonnée (Active) voit ceux de sa formule ; en essai,
    tout Professional ; suspendue, en impayé ou résiliée, tous les modules, en lecture seule
    (SubscriptionActivePermission bloque l'écriture) pour toujours accéder à ses données."""
    if clinic.subscription_status == Clinic.SubscriptionStatus.ACTIVE:
        return [module for module in PLAN_MODULES if has_feature(clinic, module)]
    return list(PLAN_MODULES)


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
