from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.filters import SearchFilter

from common.models import AuditLog
from common.permissions import IsClinicAdmin
from common.viewsets import TenantScopedMixin

from .serializers import AuditLogSerializer


class AuditLogViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only — audit entries are written exclusively via common.audit.record_audit(), never
    through this API. business/access-policy.md: "Clinic Administrator: Can access ... All Audit
    Logs" — no other role is granted access here."""

    serializer_class = AuditLogSerializer
    permission_classes = TenantScopedMixin.permission_classes + [IsClinicAdmin]
    queryset = AuditLog.objects.select_related("user", "clinic").all()
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = {
        "action": ["exact"],
        "model_name": ["exact"],
        "created_at": ["gte", "lte"],
    }
    search_fields = ["model_name", "object_id"]
