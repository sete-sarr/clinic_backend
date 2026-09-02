from django_filters.rest_framework import DjangoFilterBackend

from common.audit import record_audit
from common.models import AuditLog
from common.viewsets import TenantScopedModelViewSet
from doctors.models import Doctor
from doctors.permissions import CanManageDoctors

from .serializers import DoctorSerializer


class DoctorViewSet(TenantScopedModelViewSet):
    serializer_class = DoctorSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageDoctors]
    queryset = Doctor.objects.select_related("user", "clinic", "department").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["department", "is_active"]

    def perform_create(self, serializer):
        doctor = serializer.save(clinic=self.request.user.clinic)
        record_audit(user=self.request.user, action=AuditLog.Action.CREATE, obj=doctor)

    def perform_update(self, serializer):
        doctor = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=doctor)

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        record_audit(
            user=self.request.user, action=AuditLog.Action.ARCHIVE, obj=instance, metadata={"reason": "deactivated"}
        )
