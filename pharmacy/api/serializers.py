from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from pharmacy.models import Medication, StockBatch, StockMovement
from pharmacy.services import receive_stock_batch


class MedicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medication
        fields = [
            "id",
            "clinic",
            "name",
            "unit",
            "unit_price",
            "current_stock",
            "min_threshold",
            "max_threshold",
            "low_stock_alerted",
            "overstock_alerted",
            "is_active",
            "created_at",
            "updated_at",
        ]
        # current_stock/*_alerted sont dérivés des mouvements de stock (pharmacy/services.py) —
        # jamais modifiables directement, comme Invoice.status/subtotal côté billing.
        read_only_fields = [
            "id",
            "clinic",
            "current_stock",
            "low_stock_alerted",
            "overstock_alerted",
            "is_active",
            "created_at",
            "updated_at",
        ]


class StockBatchSerializer(serializers.ModelSerializer):
    medication_display = serializers.CharField(source="medication.name", read_only=True)

    class Meta:
        model = StockBatch
        fields = [
            "id",
            "clinic",
            "medication",
            "medication_display",
            "batch_number",
            "expiry_date",
            "received_date",
            "quantity_received",
            "quantity_remaining",
            "unit_cost",
            "supplier",
            "created_at",
        ]
        read_only_fields = ["id", "clinic", "quantity_remaining", "created_at"]

    def create(self, validated_data):
        validated_data.pop("clinic", None)
        actor = self.context["request"].user
        try:
            return receive_stock_batch(actor=actor, **validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc


class StockMovementSerializer(serializers.ModelSerializer):
    medication_display = serializers.CharField(source="medication.name", read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "clinic",
            "medication",
            "medication_display",
            "batch",
            "movement_type",
            "quantity_delta",
            "invoice",
            "reason",
            "created_by",
            "created_at",
        ]
        read_only_fields = fields
