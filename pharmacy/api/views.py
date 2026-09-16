from django.core.exceptions import ValidationError as DjangoValidationError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from common.audit import record_audit
from common.models import AuditLog
from common.viewsets import TenantScopedMixin, TenantScopedModelViewSet
from pharmacy.models import Medication, StockBatch, StockMovement
from pharmacy.permissions import CanManageMedications, CanManageStock
from pharmacy.services import adjust_stock, archive_medication, restore_medication

from .serializers import MedicationSerializer, StockBatchSerializer, StockMovementSerializer


class MedicationViewSet(TenantScopedModelViewSet):
    serializer_class = MedicationSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageMedications]
    queryset = Medication.objects.select_related("clinic").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["is_active", "low_stock_alerted", "overstock_alerted"]
    http_method_names = ["get", "post", "patch", "head", "options"]  # pas de PUT/DELETE — voir archive()

    def perform_create(self, serializer):
        medication = serializer.save(clinic=self.request.user.clinic)
        record_audit(user=self.request.user, action=AuditLog.Action.CREATE, obj=medication)

    def perform_update(self, serializer):
        medication = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=medication)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        medication = self.get_object()
        medication = archive_medication(medication=medication, actor=request.user)
        return Response(MedicationSerializer(medication).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        medication = self.get_object()
        medication = restore_medication(medication=medication, actor=request.user)
        return Response(MedicationSerializer(medication).data)


class StockBatchViewSet(
    TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Pas de update/delete générique — un lot n'est corrigé que via adjust() (audité, voir
    pharmacy/services.py::adjust_stock), jamais par une modification libre de quantity_remaining."""

    serializer_class = StockBatchSerializer
    permission_classes = TenantScopedMixin.permission_classes + [CanManageStock]
    queryset = StockBatch.objects.select_related("clinic", "medication").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["medication"]

    @action(detail=True, methods=["post"], url_path="adjust")
    def adjust(self, request, pk=None):
        batch = self.get_object()
        quantity_delta = request.data.get("quantity_delta")
        reason = request.data.get("reason", "")
        try:
            quantity_delta = int(quantity_delta)
        except (TypeError, ValueError):
            return Response({"code": 400, "message": "quantity_delta must be an integer.", "field": "quantity_delta"}, status=400)
        try:
            adjust_stock(batch=batch, actor=request.user, quantity_delta=quantity_delta, reason=reason)
        except DjangoValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return Response({"code": 400, "message": message, "field": None}, status=400)
        batch.refresh_from_db()
        return Response(StockBatchSerializer(batch).data)


class StockMovementViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Lecture seule — le ledger n'est jamais écrit directement via l'API, uniquement par les
    services (achat, dispensation/retour de facture, ajustement)."""

    serializer_class = StockMovementSerializer
    permission_classes = TenantScopedMixin.permission_classes + [CanManageStock]
    queryset = StockMovement.objects.select_related("clinic", "medication", "batch", "invoice").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["medication", "movement_type", "invoice"]
