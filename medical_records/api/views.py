from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets

from common.audit import record_audit
from common.models import AuditLog
from common.permissions import IsSameClinic, in_role
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
    permission_classes = [IsSameClinic, CanAccessMedicalRecord]
    queryset = MedicalRecord.objects.select_related("patient", "clinic").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["patient"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            return qs
        if not getattr(user, "clinic_id", None):
            return qs.none()
        qs = qs.filter(clinic_id=user.clinic_id)
        # CanAccessMedicalRecord grants patients SAFE_METHODS access; without this branch the
        # list endpoint would return every patient's medical record in the clinic — object-level
        # permission checks only run on retrieve/update, never on the list queryset (security fix,
        # docs/known-issues.md).
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
