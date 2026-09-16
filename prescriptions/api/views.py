from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from common.audit import record_audit
from common.models import AuditLog
from common.permissions import in_role
from common.viewsets import TenantScopedModelViewSet
from prescriptions.models import Prescription
from prescriptions.permissions import CanManagePrescriptions
from prescriptions.services.pdf import render_prescription_pdf

from .serializers import PrescriptionSerializer


class PrescriptionViewSet(TenantScopedModelViewSet):
    serializer_class = PrescriptionSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManagePrescriptions]
    queryset = Prescription.objects.select_related("patient", "doctor__user", "clinic").prefetch_related("items")
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
            # Un médecin prescrit toujours sous son propre nom — ne jamais faire confiance au champ
            # doctor soumis par le client, on le remplace simplement (business/access-policy.md :
            # moindre privilège).
            save_kwargs["doctor"] = doctor_profile
        prescription = serializer.save(**save_kwargs)
        record_audit(user=user, action=AuditLog.Action.CREATE, obj=prescription)

    def perform_update(self, serializer):
        prescription = serializer.save()
        action = AuditLog.Action.CANCEL if prescription.status == Prescription.Status.CANCELLED else AuditLog.Action.UPDATE
        record_audit(user=self.request.user, action=action, obj=prescription)

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        prescription = self.get_object()
        try:
            pdf_bytes = render_prescription_pdf(prescription=prescription, user=request.user)
        except PermissionError:
            return Response({"code": 403, "message": "Not allowed to access this document.", "field": None}, status=403)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="prescription-{prescription.id}.pdf"'
        return response
