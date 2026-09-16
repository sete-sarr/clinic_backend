from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated

from common.audit import record_audit
from common.models import AuditLog
from common.permissions import IsSameClinic, SubscriptionActivePermission, in_role
from medical_records.models import MedicalRecord
from medical_records.permissions import CanAccessMedicalRecord

from .serializers import MedicalRecordSerializer


class MedicalRecordViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """No create (auto-provisioned with the patient) and no delete (permissions-matrix.md: delete forbidden)."""

    serializer_class = MedicalRecordSerializer
    # IsAuthenticated est explicite ici (audit de sécurité, 2026-09-02) même si
    # CanAccessMedicalRecord rejette déjà les utilisateurs anonymes de son côté — pour rester
    # cohérent avec le style utilisé par tous les autres ViewSet à portée tenant.
    permission_classes = [IsAuthenticated, IsSameClinic, CanAccessMedicalRecord, SubscriptionActivePermission]
    queryset = MedicalRecord.objects.select_related("patient", "clinic").all()
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["patient"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            return qs
        if not getattr(user, "clinic_id", None):
            return qs.none()
        qs = qs.filter(clinic_id=user.clinic_id)
        # CanAccessMedicalRecord accorde aux patients l'accès via SAFE_METHODS ; sans cette branche,
        # l'endpoint de liste renverrait le dossier médical de tous les patients de la clinique — les
        # vérifications de permission au niveau objet ne s'exécutent que sur retrieve/update, jamais
        # sur le queryset de liste (correctif de sécurité, docs/known-issues.md).
        if in_role(user, "patient") and not in_role(user, "doctor"):
            patient_profile = getattr(user, "patient_profile", None)
            return qs.filter(patient=patient_profile) if patient_profile else qs.none()
        return qs

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        record_audit(user=request.user, action=AuditLog.Action.VIEW, obj=instance)
        return super().retrieve(request, *args, **kwargs)

    def perform_update(self, serializer):
        instance = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=instance)
