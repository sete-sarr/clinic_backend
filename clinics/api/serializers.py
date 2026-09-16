from django.core.exceptions import ValidationError
from rest_framework import serializers

from clinics.models import Clinic

MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024  # 2MB
# design-system/ : les logos sont limités à PNG/JPG/JPEG. ImageField rejette déjà tout ce qui
# n'est pas une véritable image décodable par Pillow (audit de sécurité, 2026-09-02) — ceci
# restreint davantage aux formats spécifiques autorisés par le design system, plutôt qu'à tout
# format raster valide.
ALLOWED_LOGO_FORMATS = {"PNG", "JPEG"}


def validate_logo_size(value):
    if value.size > MAX_LOGO_SIZE_BYTES:
        raise ValidationError("L'image ne doit pas dépasser 2 Mo.")


def validate_logo_format(value):
    image_format = getattr(getattr(value, "image", None), "format", None)
    if image_format not in ALLOWED_LOGO_FORMATS:
        raise ValidationError("Seuls les formats PNG et JPEG sont acceptés.")


class ClinicSerializer(serializers.ModelSerializer):
    logo_light = serializers.ImageField(
        required=False, allow_null=True, validators=[validate_logo_size, validate_logo_format]
    )
    logo_dark = serializers.ImageField(
        required=False, allow_null=True, validators=[validate_logo_size, validate_logo_format]
    )
    logo_print = serializers.ImageField(
        required=False, allow_null=True, validators=[validate_logo_size, validate_logo_format]
    )
    favicon = serializers.ImageField(
        required=False, allow_null=True, validators=[validate_logo_size, validate_logo_format]
    )

    class Meta:
        model = Clinic
        fields = [
            "id", "name", "address", "phone", "email", "is_active", "created_at", "updated_at",
            "subscription_status", "plan_tier", "billing_cycle", "trial_ends_at", "current_period_end",
            "locale", "logo_light", "logo_dark", "logo_print", "favicon",
        ]
        # Les champs d'abonnement sont en lecture seule ici : leur mutation ne se fait que via le
        # webhook Stripe ou les appels subscriptions.services.change_subscription_status/change_plan
        # protégés par le Django admin — jamais via un PATCH direct sur cet endpoint.
        # stripe_customer_id/stripe_subscription_id sont volontairement exclus de `fields` en
        # totalité (aucun besoin côté frontend, éviter de divulguer les ID d'objets Stripe même en
        # lecture seule).
        # locale/logo_*/favicon ne sont volontairement PAS en lecture seule — le clinic_admin les
        # modifie via ce même endpoint (ClinicViewSet.get_permissions() restreint déjà
        # update/partial_update à IsClinicAdmin, voir backend/clinics/api/views.py).
        read_only_fields = [
            "id", "created_at", "updated_at",
            "subscription_status", "plan_tier", "billing_cycle", "trial_ends_at", "current_period_end",
        ]


class ClinicPublicSerializer(serializers.ModelSerializer):
    """Sélecteur de clinique pré-authentification pour l'activation du compte patient — id/name
    uniquement, jamais l'address/phone/email exposés par ClinicSerializer (rien de ce qui est
    pré-authentification ne doit les divulguer)."""

    class Meta:
        model = Clinic
        fields = ["id", "name"]
