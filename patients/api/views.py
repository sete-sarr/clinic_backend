from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from common.audit import record_audit
from common.exports import CsvExportMixin
from common.models import AuditLog
from common.permissions import in_role
from common.viewsets import TenantScopedModelViewSet
from patients.models import Patient
from patients.permissions import CanManagePatients
from patients.services.pdf import render_patient_statement_pdf

from .serializers import PatientSerializer


class PatientViewSet(CsvExportMixin, TenantScopedModelViewSet):
    serializer_class = PatientSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManagePatients]
    queryset = Patient.objects.select_related("clinic").all()
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["is_active", "gender"]
    search_fields = ["patient_number", "first_name", "last_name", "phone", "national_id"]
    export_fields = [
        "patient_number",
        "first_name",
        "last_name",
        "phone",
        "email",
        "date_of_birth",
        "gender",
        "blood_type",
        "is_active",
    ]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if in_role(user, "patient") and not in_role(user, "doctor", "secretary", "accountant", "clinic_admin"):
            patient_profile = getattr(user, "patient_profile", None)
            return qs.filter(id=patient_profile.id) if patient_profile else qs.none()
        return qs

    def perform_create(self, serializer):
        patient = serializer.save(clinic=self.request.user.clinic)
        record_audit(user=self.request.user, action=AuditLog.Action.CREATE, obj=patient)

    def perform_update(self, serializer):
        patient = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=patient)

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        record_audit(
            user=self.request.user, action=AuditLog.Action.ARCHIVE, obj=instance, metadata={"reason": "deactivated"}
        )

    @action(detail=True, methods=["get"], url_path="statement-pdf")
    def statement_pdf(self, request, pk=None):
        patient = self.get_object()
        date_from = request.query_params.get("date_from") or None
        date_to = request.query_params.get("date_to") or None
        try:
            pdf_bytes = render_patient_statement_pdf(
                patient=patient, user=request.user, date_from=date_from, date_to=date_to
            )
        except PermissionError:
            return Response({"code": 403, "message": "Not allowed to access this document.", "field": None}, status=403)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="statement-{patient.patient_number}.pdf"'
        return response
