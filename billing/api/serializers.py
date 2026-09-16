from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from billing.models import Invoice, InvoiceLine
from billing.services import create_invoice, update_invoice


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = ["id", "description", "quantity", "unit_price", "line_total"]
        read_only_fields = ["id", "line_total"]


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    amount_paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    # Facilités en lecture seule pour les UI de liste/détail — réutilisent le select_related
    # ("patient", "doctor__user") déjà présent sur le queryset (voir InvoiceViewSet), donc
    # n'ajoutent aucune requête supplémentaire.
    patient_display = serializers.SerializerMethodField()
    doctor_display = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id",
            "clinic",
            "patient",
            "patient_display",
            "doctor",
            "doctor_display",
            "number",
            "issue_date",
            "subtotal",
            "vat_rate",
            "vat_amount",
            "total_amount",
            "status",
            "amount_paid",
            "balance_due",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "clinic",
            "number",
            "subtotal",
            "vat_amount",
            "total_amount",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_patient_display(self, obj):
        return f"{obj.patient.first_name} {obj.patient.last_name} ({obj.patient.patient_number})"

    def get_doctor_display(self, obj):
        if not obj.doctor:
            return ""
        return obj.doctor.user.get_full_name() or obj.doctor.user.get_username()

    def create(self, validated_data):
        clinic = validated_data.pop("clinic")
        lines = validated_data.pop("lines")
        try:
            return create_invoice(clinic=clinic, lines=lines, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def update(self, instance, validated_data):
        validated_data.pop("clinic", None)
        lines = validated_data.pop("lines", None)
        try:
            return update_invoice(invoice=instance, lines=lines, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
