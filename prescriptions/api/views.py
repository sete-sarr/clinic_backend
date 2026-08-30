from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

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
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status", "patient", "doctor"]

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
            # A doctor always prescribes under their own name — never trust a client-submitted
            # doctor field here, just override it (business/access-policy.md: least privilege).
            save_kwargs["doctor"] = doctor_profile
        serializer.save(**save_kwargs)

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
