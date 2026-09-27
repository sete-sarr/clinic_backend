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

    # Les contraintes de base (unique_medication_name_per_clinic,
    # medication_max_threshold_gte_min_threshold) restent le filet de sécurité ; elles sont
    # revérifiées ici pour renvoyer un 400 {code, message, field} au lieu d'une IntegrityError (500).
    def validate_name(self, value):
        request = self.context.get("request")
        clinic_id = self.instance.clinic_id if self.instance else getattr(request.user, "clinic_id", None)
        # Insensible à la casse : "Doliprane" et "doliprane" désignent le même produit au catalogue.
        duplicates = Medication.objects.filter(clinic_id=clinic_id, name__iexact=value)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("Un médicament portant ce nom existe déjà dans cette clinique.")
        return value

    def validate(self, attrs):
        # PATCH partiel : comparer aux valeurs déjà enregistrées pour le champ non envoyé.
        min_threshold = attrs.get("min_threshold", getattr(self.instance, "min_threshold", 0))
        max_threshold = attrs.get("max_threshold", getattr(self.instance, "max_threshold", None))
        if max_threshold is not None and max_threshold < (min_threshold or 0):
            raise serializers.ValidationError(
                {"max_threshold": "Le seuil maximal doit être supérieur ou égal au seuil minimal."}
            )
        return attrs


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
