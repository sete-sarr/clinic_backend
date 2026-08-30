from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from payments.models import Payment
from payments.services import create_payment


class PaymentSerializer(serializers.ModelSerializer):
    # Read-only conveniences for list/detail UIs — reuse the queryset's existing
    # select_related("invoice", "invoice__patient") (see PaymentViewSet), so these add no extra queries.
    invoice_number = serializers.SerializerMethodField()
    patient_display = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id",
            "clinic",
            "invoice",
            "invoice_number",
            "patient_display",
            "amount",
            "method",
            "status",
            "date",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "clinic", "status", "created_by", "created_at", "updated_at"]

    def get_invoice_number(self, obj):
        return obj.invoice.number

    def get_patient_display(self, obj):
        patient = obj.invoice.patient
        return f"{patient.first_name} {patient.last_name} ({patient.patient_number})"

    def create(self, validated_data):
        clinic = validated_data.pop("clinic")
        created_by = self.context["request"].user
        try:
            return create_payment(clinic=clinic, created_by=created_by, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
