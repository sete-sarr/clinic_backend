from django.db import models
from django.db.models.functions import Lower

from common.currency import CURRENCY_CHOICES, DEFAULT_CURRENCY
from common.models import TimeStampedModel


def clinic_logo_upload_path(instance, filename):
    # Conservé uniquement pour les anciennes migrations (0003) : les logos ne sont plus des fichiers
    # mais des ClinicLogo stockés en base (voir plus bas).
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

    # Branding / préférences (design-system/branding.md). locale : langue des e-mails, SMS et PDF
    # destinés aux patients, et langue par défaut du personnel (docs/i18n.md §2).
    locale = models.CharField(max_length=2, choices=Locale.choices, default=Locale.FRENCH)
    # Devise de facturation des patients, choisie par le clinic_admin (docs/i18n.md §8). Copiée sur
    # chaque facture à sa création : la changer n'affecte que les factures suivantes.
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=DEFAULT_CURRENCY)

    # Tarification des nuitées d'hospitalisation (docs/hospitalization.md §3), choisie par le
    # clinic_admin dans Paramètres : forfait unique (inpatient_nightly_rate) ou tarif du type de la
    # chambre occupée (hospitalization.RoomType.nightly_rate). Appliquée aux sorties suivantes
    # uniquement : le tarif est figé sur la ligne de facture à la sortie.
    class InpatientBillingMode(models.TextChoices):
        FLAT = "flat", "Flat rate"
        PER_ROOM_TYPE = "per_room_type", "Per room type"

    inpatient_billing_mode = models.CharField(
        max_length=16, choices=InpatientBillingMode.choices, default=InpatientBillingMode.FLAT
    )
    inpatient_nightly_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

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


class ClinicLogo(models.Model):
    """Logo d'une clinique, stocké en base (PNG/JPEG, 2 Mo max — clinics/api/serializers.py).

    Les fichiers téléversés ne survivaient pas en production : le disque de l'hébergeur est effacé à
    chaque redéploiement et /media/ n'y est pas servi. Modèle séparé de Clinic pour ne jamais
    charger ces octets lors des nombreuses lectures de la clinique. Diffusion : URL signée
    (clinics/services.py) ; PDF : data URI."""

    class Kind(models.TextChoices):
        LIGHT = "light"
        DARK = "dark"
        PRINT = "print"
        FAVICON = "favicon"

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="logos")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    content = models.BinaryField()
    content_type = models.CharField(max_length=32)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["clinic", "kind"], name="unique_logo_kind_per_clinic")]

    def __str__(self):
        return f"{self.clinic_id}:{self.kind}"
