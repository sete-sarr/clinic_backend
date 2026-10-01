from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework import serializers

from clinics.models import Clinic
from clinics.services import LOGO_FIELDS, logo_url, logos_without_content, update_clinic_logos

MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024  # 2MB
# design-system/ : les logos sont limités à PNG/JPG/JPEG. ImageField rejette déjà tout ce qui
# n'est pas une véritable image décodable par Pillow (audit de sécurité, 2026-09-02) — ceci
# restreint davantage aux formats spécifiques autorisés par le design system, plutôt qu'à tout
# format raster valide.
ALLOWED_LOGO_FORMATS = {"PNG", "JPEG"}


def validate_logo_size(value):
    if value.size > MAX_LOGO_SIZE_BYTES:
        raise ValidationError(_("L'image ne doit pas dépasser 2 Mo."))


def validate_logo_format(value):
    image_format = getattr(getattr(value, "image", None), "format", None)
    if image_format not in ALLOWED_LOGO_FORMATS:
        raise ValidationError(_("Seuls les formats PNG et JPEG sont acceptés."))


def _logo_field():
    # Écriture seule : un fichier PNG/JPEG (ou null pour retirer le logo). En lecture, le champ de
    # même nom contient l'URL signée du logo (to_representation) — contrat inchangé pour le frontend.
    return serializers.ImageField(
        required=False, allow_null=True, write_only=True, validators=[validate_logo_size, validate_logo_format]
    )


class ClinicSerializer(serializers.ModelSerializer):
    logo_light = _logo_field()
    logo_dark = _logo_field()
    logo_print = _logo_field()
    favicon = _logo_field()

    class Meta:
        model = Clinic
        fields = [
            "id", "name", "address", "phone", "email", "is_active", "created_at", "updated_at",
            "subscription_status", "plan_tier", "billing_cycle", "trial_ends_at", "current_period_end",
            "locale", "currency", "logo_light", "logo_dark", "logo_print", "favicon",
        ]
        # Les champs d'abonnement sont en lecture seule ici : leur mutation ne se fait que via le
        # webhook Stripe ou les appels subscriptions.services.change_subscription_status/change_plan
        # protégés par le Django admin — jamais via un PATCH direct sur cet endpoint.
        # stripe_customer_id/stripe_subscription_id sont volontairement exclus de `fields` en
        # totalité (aucun besoin côté frontend, éviter de divulguer les ID d'objets Stripe même en
        # lecture seule).
        # locale/currency/logo_*/favicon ne sont volontairement PAS en lecture seule — le clinic_admin les
        # modifie via ce même endpoint (ClinicViewSet.get_permissions() restreint déjà
        # update/partial_update à IsClinicAdmin, voir backend/clinics/api/views.py).
        read_only_fields = [
            "id", "created_at", "updated_at",
            "subscription_status", "plan_tier", "billing_cycle", "trial_ends_at", "current_period_end",
        ]

    def update(self, instance, validated_data):
        # Logos stockés en base (clinics/services.py) : retirés des données du modèle Clinic.
        logo_changes = {field: validated_data.pop(field) for field in LOGO_FIELDS if field in validated_data}
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if logo_changes:
                update_clinic_logos(clinic=instance, changes=logo_changes)
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        logos = logos_without_content(instance)
        request = self.context.get("request")
        for field, kind in LOGO_FIELDS.items():
            logo = logos.get(kind)
            data[field] = logo_url(logo=logo, request=request) if logo else None
        return data


class ClinicPublicSerializer(serializers.ModelSerializer):
    """Sélecteur de clinique pré-authentification pour l'activation du compte patient — id/name
    uniquement, jamais l'address/phone/email exposés par ClinicSerializer (rien de ce qui est
    pré-authentification ne doit les divulguer)."""

    class Meta:
        model = Clinic
        fields = ["id", "name"]
