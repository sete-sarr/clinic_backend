from django.db import models
from django.db.models.functions import Lower

from common.models import TimeStampedModel


def clinic_logo_upload_path(instance, filename):
    # Chemin scopé par tenant (isolation multi-tenant de CLAUDE.md) — évite les collisions de noms
    # de fichiers entre cliniques. instance.pk existe toujours ici : les lignes Clinic sont
    # provisionnées une seule fois à l'onboarding du tenant, cet upload_to n'est jamais atteint
    # que via un PATCH sur une clinique déjà existante.
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

    # Branding / préférences (design-system/branding.md). locale ne fait pour l'instant que
    # stocker la préférence — aucune traduction en runtime n'existe encore, l'UI reste en
    # français quelle que soit cette valeur.
    locale = models.CharField(max_length=2, choices=Locale.choices, default=Locale.FRENCH)
    logo_light = models.ImageField(upload_to=clinic_logo_upload_path, blank=True, null=True)
    logo_dark = models.ImageField(upload_to=clinic_logo_upload_path, blank=True, null=True)
    logo_print = models.ImageField(upload_to=clinic_logo_upload_path, blank=True, null=True)
    favicon = models.ImageField(upload_to=clinic_logo_upload_path, blank=True, null=True)

    # Facturation de l'abonnement à la plateforme (business/subscription-billing-policy.md) — la
    # clinique payant pour son propre usage de la plateforme, entièrement distinct de
    # backend/billing/ (la clinique facturant ses patients). Aucun montant en dollars n'est stocké
    # ici : plan_tier/billing_cycle ne font que pointer vers le catalogue de Price ID Stripe
    # (subscriptions/catalog.py) ; l'objet Price de Stripe lui-même est la source de vérité pour
    # le montant réel facturé.
    subscription_status = models.CharField(
        max_length=16, choices=SubscriptionStatus.choices, default=SubscriptionStatus.TRIAL
    )
    plan_tier = models.CharField(max_length=16, choices=PlanTier.choices, default=PlanTier.STARTER)
    billing_cycle = models.CharField(max_length=8, choices=BillingCycle.choices, default=BillingCycle.MONTHLY)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    # Idempotence de la notification SUBSCRIPTION EXPIRING (calendrier à 4 points de
    # business/notification-rules.md) — réinitialisé à null chaque fois que current_period_end
    # avance (renouvellement), afin que les rappels du cycle suivant se déclenchent à nouveau au
    # lieu de rester définitivement "déjà notifié".
    expiring_notified_30d_at = models.DateTimeField(null=True, blank=True)
    expiring_notified_15d_at = models.DateTimeField(null=True, blank=True)
    expiring_notified_7d_at = models.DateTimeField(null=True, blank=True)
    expiring_notified_1d_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="unique_clinic_name_ci"),
        ]

    def __str__(self):
        return self.name
