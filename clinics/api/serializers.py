from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework import serializers

from clinics.models import Clinic
from common.images import validate_image_format, validate_image_size
from common.permissions import in_role
from clinics.services import LOGO_FIELDS, logo_url, logos_without_content, update_clinic_logos


def _logo_field():
    # Écriture seule : un fichier PNG/JPEG (ou null pour retirer le logo). En lecture, le champ de
    # même nom contient l'URL signée du logo (to_representation) — contrat inchangé pour le frontend.
    return serializers.ImageField(
        required=False, allow_null=True, write_only=True, validators=[validate_image_size, validate_image_format]
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
            "inpatient_billing_mode", "inpatient_nightly_rate",
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

    def validate_inpatient_nightly_rate(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError(_("Le forfait de nuitée doit être strictement positif."))
        return value

    def validate(self, attrs):
        # business/validation-rules.md § VALIDATION HOSPITALISATION : en mode forfait, le forfait est requis.
        mode = attrs.get("inpatient_billing_mode", getattr(self.instance, "inpatient_billing_mode", None))
        rate = attrs.get("inpatient_nightly_rate", getattr(self.instance, "inpatient_nightly_rate", None))
        if mode == Clinic.InpatientBillingMode.FLAT and "inpatient_billing_mode" in attrs and rate is None:
            raise serializers.ValidationError(
                {"inpatient_nightly_rate": _("Indiquez le forfait de nuitée pour le mode « forfait unique ».")}
            )
        return attrs

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
        # Tarifs de nuitée : donnée financière, jamais montrée à l'infirmier ni au reste du personnel
        # clinique (business/access-policy.md) — seulement à l'administrateur et à la comptabilité.
        user = getattr(request, "user", None)
        if not (user and (user.is_superuser or in_role(user, "clinic_admin", "accountant"))):
            data.pop("inpatient_nightly_rate", None)
        return data


class ClinicPublicSerializer(serializers.ModelSerializer):
    """Sélecteur de clinique pré-authentification pour l'activation du compte patient — id/name
    uniquement, jamais l'address/phone/email exposés par ClinicSerializer (rien de ce qui est
    pré-authentification ne doit les divulguer)."""

    class Meta:
        model = Clinic
        fields = ["id", "name"]
