import django_filters
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from appointments.models import Appointment
from appointments.pdf import render_checkin_ticket_pdf
from appointments.permissions import CanManageAppointments
from appointments.services import (
    assign_appointment,
    cancel_appointment_by_patient,
    check_in_appointment,
    doctor_appointments_q,
    take_appointment,
)
from common.audit import record_audit
from common.exports import CsvExportMixin
from common.models import AuditLog
from common.permissions import in_role
from common.viewsets import TenantScopedModelViewSet
from doctors.models import Doctor
from patients.access import ensure_patient_accessible

from .serializers import AppointmentSerializer


class AppointmentFilterSet(django_filters.FilterSet):
    # Permet à un médecin/secrétaire de retrouver le rendez-vous d'un patient via le patient_number
    # imprimé sur son ticket d'enregistrement (besoin métier : ce filtre n'existait pas avant
    # cette fonctionnalité — la recherche par patient_number n'existait auparavant que sur la
    # liste/le sélecteur de patients, pas ici).
    patient_number = django_filters.CharFilter(field_name="patient__patient_number", lookup_expr="icontains")
    checked_in = django_filters.BooleanFilter(method="filter_checked_in")
    # assigned=false : rendez-vous de département encore sans médecin (« À attribuer »).
    assigned = django_filters.BooleanFilter(field_name="doctor", lookup_expr="isnull", exclude=True)

    class Meta:
        model = Appointment
        fields = ["status", "date", "doctor", "department", "patient"]

    def filter_checked_in(self, queryset, name, value):
        return queryset.filter(checked_in_at__isnull=not value)


class AppointmentAssignSerializer(serializers.Serializer):
    # null : remet le rendez-vous à la disposition du département. Le tenant est vérifié par le service.
    doctor = serializers.PrimaryKeyRelatedField(queryset=Doctor.objects.filter(is_active=True), allow_null=True)


class AppointmentViewSet(CsvExportMixin, TenantScopedModelViewSet):
    serializer_class = AppointmentSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageAppointments]
    queryset = Appointment.objects.select_related("patient", "doctor__user", "department", "clinic").all()
    filter_backends = [DjangoFilterBackend]
    filterset_class = AppointmentFilterSet
    export_fields = ["date", "time", "status", "patient_display", "doctor_display", "reason"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if in_role(user, "doctor") and not in_role(user, "clinic_admin", "secretary"):
            doctor_profile = getattr(user, "doctor_profile", None)
            return qs.filter(doctor_appointments_q(doctor_profile)) if doctor_profile else qs.none()
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
                raise PermissionDenied(_("Un médecin ne peut créer des rendez-vous qu'à son propre nom."))
            if serializer.validated_data.get("patient") is not None:
                ensure_patient_accessible(user, serializer.validated_data["patient"])

        save_kwargs = {"clinic": user.clinic}
        if in_role(user, "patient") and not in_role(user, "doctor", "secretary", "accountant", "clinic_admin"):
            patient_profile = getattr(user, "patient_profile", None)
            if patient_profile is None:
                raise PermissionDenied(_("Aucun profil patient n'est associé à ce compte."))
            save_kwargs["patient"] = patient_profile
        elif serializer.validated_data.get("patient") is None:
            raise serializers.ValidationError({"patient": [_("Ce champ est obligatoire.")]})

        appointment = serializer.save(**save_kwargs)
        record_audit(user=user, action=AuditLog.Action.CREATE, obj=appointment)

    def perform_update(self, serializer):
        appointment = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=appointment)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        appointment = self.get_object()  # applique le scoping de get_queryset + has_object_permission
        try:
            appointment = cancel_appointment_by_patient(appointment=appointment)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        record_audit(user=request.user, action=AuditLog.Action.CANCEL, obj=appointment)
        return Response(self.get_serializer(appointment).data)

    @action(detail=True, methods=["post"])
    def take(self, request, pk=None):
        """Un médecin du département prend en charge un rendez-vous encore à attribuer."""
        appointment = self.get_object()
        doctor_profile = getattr(request.user, "doctor_profile", None)
        if doctor_profile is None:
            raise PermissionDenied(_("Aucun profil médecin n'est associé à ce compte."))
        try:
            appointment = take_appointment(appointment=appointment, doctor=doctor_profile)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        record_audit(user=request.user, action=AuditLog.Action.UPDATE, obj=appointment,
                     metadata={"taken_by_doctor": doctor_profile.id})
        return Response(self.get_serializer(appointment).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        """L'accueil attribue, réattribue ou remet au département (doctor=null) un rendez-vous."""
        appointment = self.get_object()
        payload = AppointmentAssignSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            appointment, previous_doctor_id = assign_appointment(
                appointment=appointment, doctor=payload.validated_data["doctor"]
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        record_audit(user=request.user, action=AuditLog.Action.UPDATE, obj=appointment,
                     metadata={"assigned_doctor": appointment.doctor_id, "previous_doctor": previous_doctor_id})
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
            return Response({"code": 403, "message": _("Vous n'êtes pas autorisé(e) à accéder à ce document."), "field": None}, status=403)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="ticket-{appointment.ticket_number}.pdf"'
        return response
