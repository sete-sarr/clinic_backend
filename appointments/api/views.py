import django_filters
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from appointments.models import Appointment
from appointments.pdf import render_checkin_ticket_pdf
from appointments.permissions import CanManageAppointments
from appointments.services import cancel_appointment_by_patient, check_in_appointment
from common.audit import record_audit
from common.exports import CsvExportMixin
from common.models import AuditLog
from common.permissions import in_role
from common.viewsets import TenantScopedModelViewSet

from .serializers import AppointmentSerializer


class AppointmentFilterSet(django_filters.FilterSet):
    # Lets a doctor/secretary find a patient's appointment by the patient_number printed on their
    # check-in ticket (business need: no such filter existed before this feature — patient_number
    # search previously only existed on the Patients list/picker, not here).
    patient_number = django_filters.CharFilter(field_name="patient__patient_number", lookup_expr="icontains")
    checked_in = django_filters.BooleanFilter(method="filter_checked_in")

    class Meta:
        model = Appointment
        fields = ["status", "date", "doctor", "patient"]

    def filter_checked_in(self, queryset, name, value):
        return queryset.filter(checked_in_at__isnull=not value)


class AppointmentViewSet(CsvExportMixin, TenantScopedModelViewSet):
    serializer_class = AppointmentSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageAppointments]
    queryset = Appointment.objects.select_related("patient", "doctor__user", "doctor__department", "clinic").all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = AppointmentFilterSet
    export_fields = ["date", "time", "status", "patient_display", "doctor_display", "reason"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if in_role(user, "doctor") and not in_role(user, "clinic_admin", "secretary"):
            doctor_profile = getattr(user, "doctor_profile", None)
            return qs.filter(doctor=doctor_profile) if doctor_profile else qs.none()
        if in_role(user, "patient") and not in_role(user, "doctor", "secretary", "accountant", "clinic_admin"):
            patient_profile = getattr(user, "patient_profile", None)
            return qs.filter(patient=patient_profile) if patient_profile else qs.none()
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if in_role(user, "doctor") and not in_role(user, "clinic_admin", "secretary"):
            doctor_profile = getattr(user, "doctor_profile", None)
            requested_doctor = serializer.validated_data.get("doctor")
            if doctor_profile is None or requested_doctor is None or requested_doctor.id != doctor_profile.id:
                raise PermissionDenied("A doctor can only create appointments under their own name.")

        save_kwargs = {"clinic": user.clinic}
        if in_role(user, "patient") and not in_role(user, "doctor", "secretary", "accountant", "clinic_admin"):
            patient_profile = getattr(user, "patient_profile", None)
            if patient_profile is None:
                raise PermissionDenied("No patient profile linked to this account.")
            save_kwargs["patient"] = patient_profile
        elif serializer.validated_data.get("patient") is None:
            raise serializers.ValidationError({"patient": ["This field is required."]})

        appointment = serializer.save(**save_kwargs)
        record_audit(user=user, action=AuditLog.Action.CREATE, obj=appointment)

    def perform_update(self, serializer):
        appointment = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=appointment)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        appointment = self.get_object()  # applies get_queryset scoping + has_object_permission
        try:
            appointment = cancel_appointment_by_patient(appointment=appointment)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        record_audit(user=request.user, action=AuditLog.Action.CANCEL, obj=appointment)
        return Response(self.get_serializer(appointment).data)

    @action(detail=True, methods=["post"], url_path="check-in")
    def check_in(self, request, pk=None):
        appointment = self.get_object()
        try:
            appointment = check_in_appointment(appointment=appointment)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        record_audit(user=request.user, action=AuditLog.Action.UPDATE, obj=appointment, metadata={"checked_in": True})
        return Response(self.get_serializer(appointment).data)

    @action(detail=True, methods=["get"], url_path="ticket-pdf")
    def ticket_pdf(self, request, pk=None):
        appointment = self.get_object()
        try:
            pdf_bytes = render_checkin_ticket_pdf(appointment=appointment, user=request.user)
        except PermissionError:
            return Response({"code": 403, "message": "Not allowed to access this document.", "field": None}, status=403)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="ticket-{appointment.ticket_number}.pdf"'
        return response
