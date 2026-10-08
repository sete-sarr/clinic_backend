from django.http import HttpResponse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from django.utils.translation import gettext as _
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from common.audit import record_audit
from common.exports import CsvExportMixin
from common.models import AuditLog
from common.permissions import in_role
from common.photos import owner_photo_url, remove_photo, set_photo
from common.viewsets import TenantScopedModelViewSet
from patients.models import Patient, PatientPhoto
from patients.permissions import CanEditPatientPhoto, CanManagePatients
from patients.services.pdf import render_patient_statement_pdf

from .serializers import PatientPhotoUploadSerializer, PatientSerializer


class PatientViewSet(CsvExportMixin, TenantScopedModelViewSet):
    serializer_class = PatientSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManagePatients]
    queryset = Patient.objects.select_related("clinic", "photo")
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
            return Response({"code": 403, "message": _("Vous n'êtes pas autorisé(e) à accéder à ce document."), "field": None}, status=403)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="statement-{patient.patient_number}.pdf"'
        return response

    @action(
        detail=True,
        methods=["post", "delete"],
        permission_classes=TenantScopedModelViewSet.permission_classes + [CanEditPatientPhoto],
    )
    def photo(self, request, pk=None):
        """POST : ajoute ou remplace la photo (consentement obligatoire) ; DELETE : la retire."""
        patient = self.get_object()
        if request.method == "DELETE":
            remove_photo(model=PatientPhoto, owner=patient, actor=request.user)
        else:
            serializer = PatientPhotoUploadSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            set_photo(
                model=PatientPhoto,
                owner=patient,
                uploaded_file=serializer.validated_data["photo"],
                actor=request.user,
                consented_at=timezone.now(),
                consent_recorded_by=request.user,
            )
        return Response({"photo": owner_photo_url(patient, request)})
