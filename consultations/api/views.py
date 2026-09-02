from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter

from common.audit import record_audit
from common.models import AuditLog
from common.permissions import in_role
from common.viewsets import TenantScopedModelViewSet
from consultations.models import Consultation
from consultations.permissions import CanManageConsultations

from .serializers import ConsultationSerializer


class ConsultationViewSet(TenantScopedModelViewSet):
    serializer_class = ConsultationSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageConsultations]
    queryset = Consultation.objects.select_related("patient", "doctor__user", "clinic").all()
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["status", "patient", "doctor"]
    search_fields = ["patient__first_name", "patient__last_name", "patient__patient_number"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if in_role(user, "doctor") and not in_role(user, "clinic_admin"):
            doctor_profile = getattr(user, "doctor_profile", None)
            return qs.filter(doctor=doctor_profile) if doctor_profile else qs.none()
        if in_role(user, "patient") and not in_role(user, "doctor", "clinic_admin"):
            patient_profile = getattr(user, "patient_profile", None)
            return qs.filter(patient=patient_profile) if patient_profile else qs.none()
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        save_kwargs = {"clinic": user.clinic}
        if in_role(user, "doctor") and not in_role(user, "clinic_admin"):
            doctor_profile = getattr(user, "doctor_profile", None)
            if doctor_profile is None:
                raise PermissionDenied("This account has no doctor profile in this clinic.")
            # A doctor always creates their own consultations — never trust a client-submitted
            # doctor field here, just override it (business/access-policy.md: least privilege).
            save_kwargs["doctor"] = doctor_profile
        consultation = serializer.save(**save_kwargs)
        record_audit(user=user, action=AuditLog.Action.CREATE, obj=consultation)

    def perform_update(self, serializer):
        consultation = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=consultation)
