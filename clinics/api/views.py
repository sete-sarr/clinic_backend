from rest_framework import generics, mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated

from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog
from common.permissions import IsClinicAdmin

from .serializers import ClinicPublicSerializer, ClinicSerializer


class ClinicViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Clinic is the tenant itself: staff only ever see their own clinic, and only
    a clinic admin (or platform superuser) may edit it. No create/delete here —
    provisioning a new tenant is a platform-level operation, not a business one.
    """

    serializer_class = ClinicSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Clinic.objects.all()
        if user.clinic_id:
            return Clinic.objects.filter(pk=user.clinic_id)
        return Clinic.objects.none()

    def get_permissions(self):
        if self.action in ("update", "partial_update"):
            return [IsAuthenticated(), IsClinicAdmin()]
        return super().get_permissions()

    def perform_update(self, serializer):
        clinic = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=clinic)


class ClinicPublicListView(generics.ListAPIView):
    """Pre-auth clinic picker for patient account activation (features/patient-portal/activation).
    id/name only — see ClinicPublicSerializer for why this must never reuse ClinicSerializer."""

    serializer_class = ClinicPublicSerializer
    permission_classes = [AllowAny]
    queryset = Clinic.objects.filter(is_active=True)
    filter_backends = [SearchFilter]
    search_fields = ["name"]
