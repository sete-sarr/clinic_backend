from django.core.exceptions import ValidationError
from rest_framework import serializers

from clinics.models import Clinic

MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024  # 2MB


def validate_logo_size(value):
    if value.size > MAX_LOGO_SIZE_BYTES:
        raise ValidationError("L'image ne doit pas dépasser 2 Mo.")


class ClinicSerializer(serializers.ModelSerializer):
    logo_light = serializers.ImageField(required=False, allow_null=True, validators=[validate_logo_size])
    logo_dark = serializers.ImageField(required=False, allow_null=True, validators=[validate_logo_size])
    logo_print = serializers.ImageField(required=False, allow_null=True, validators=[validate_logo_size])
    favicon = serializers.ImageField(required=False, allow_null=True, validators=[validate_logo_size])

    class Meta:
        model = Clinic
        fields = [
            "id", "name", "address", "phone", "email", "is_active", "created_at", "updated_at",
            "subscription_status", "plan_tier", "billing_cycle", "trial_ends_at", "current_period_end",
            "locale", "logo_light", "logo_dark", "logo_print", "favicon",
        ]
        # Subscription fields are read-only here: mutation only happens via the Stripe webhook or
        # the Django-admin-gated subscriptions.services.change_subscription_status/change_plan
        # calls — never through a direct PATCH on this endpoint. stripe_customer_id/
        # stripe_subscription_id are deliberately excluded from `fields` entirely (no frontend
        # need, avoid leaking Stripe object IDs even read-only).
        # locale/logo_*/favicon are deliberately NOT read-only — clinic_admin edits them via this
        # same endpoint (ClinicViewSet.get_permissions() already gates update/partial_update to
        # IsClinicAdmin, see backend/clinics/api/views.py).
        read_only_fields = [
            "id", "created_at", "updated_at",
            "subscription_status", "plan_tier", "billing_cycle", "trial_ends_at", "current_period_end",
        ]


class ClinicPublicSerializer(serializers.ModelSerializer):
    """Pre-auth clinic picker for patient account activation — id/name only, never the
    address/phone/email exposed by ClinicSerializer (nothing pre-auth should leak those)."""

    class Meta:
        model = Clinic
        fields = ["id", "name"]
