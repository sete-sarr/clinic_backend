from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch
from django.http import HttpResponse
from django.utils.translation import gettext as _
from django_filters import rest_framework as filters
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from common.audit import record_audit
from common.models import AuditLog
from common.viewsets import TenantScopedMixin, TenantScopedModelViewSet
from hospitalization import services
from hospitalization.models import Admission, Bed, BedTransfer, Room, RoomType
from hospitalization.pdf import render_stay_summary_pdf
from hospitalization.permissions import CanAccessAdmissions, CanManageWardStructure

from .serializers import (
    AdmissionCreateSerializer,
    AdmissionSerializer,
    BedChoiceSerializer,
    BedSerializer,
    DischargeSerializer,
    NursingNoteSerializer,
    RoomSerializer,
    RoomTypeSerializer,
    VitalSignSerializer,
)


def _service_call(func, **kwargs):
    try:
        return func(**kwargs)
    except DjangoValidationError as exc:
        detail = exc.message_dict if hasattr(exc, "error_dict") else exc.messages
        raise serializers.ValidationError(detail) from exc


class _AuditedStructureViewSet(TenantScopedModelViewSet):
    """Paramétrage des chambres et lits (administrateur) : création / modification auditées,
    archivage au lieu de la suppression."""

    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageWardStructure]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filter_backends = [filters.DjangoFilterBackend]
    filterset_fields = ["is_active"]

    def perform_create(self, serializer):
        instance = serializer.save(clinic=self.request.user.clinic)
        record_audit(user=self.request.user, action=AuditLog.Action.CREATE, obj=instance)

    def perform_update(self, serializer):
        instance = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=instance)


class RoomTypeViewSet(_AuditedStructureViewSet):
    serializer_class = RoomTypeSerializer
    queryset = RoomType.objects.all()

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self._set_active(False)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        return self._set_active(True)

    def _set_active(self, active):
        room_type = self.get_object()
        if not active and room_type.rooms.filter(is_active=True).exists():
            raise serializers.ValidationError(_("Ce type est encore utilisé par des chambres actives."))
        room_type.is_active = active
        room_type.save(update_fields=["is_active", "updated_at"])
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE if active else AuditLog.Action.ARCHIVE, obj=room_type)
        return Response(self.get_serializer(room_type).data)


class RoomViewSet(_AuditedStructureViewSet):
    serializer_class = RoomSerializer
    queryset = Room.objects.select_related("department", "room_type")
    filterset_fields = ["is_active", "department"]

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        room = _service_call(services.set_room_active, room=self.get_object(), actor=request.user, active=False)
        return Response(self.get_serializer(room).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        room = _service_call(services.set_room_active, room=self.get_object(), actor=request.user, active=True)
        return Response(self.get_serializer(room).data)


class BedViewSet(_AuditedStructureViewSet):
    serializer_class = BedSerializer
    queryset = Bed.objects.select_related("room__department", "room__room_type").prefetch_related(
        Prefetch(
            "admissions",
            queryset=Admission.objects.filter(status=Admission.Status.ADMITTED).select_related("patient"),
            to_attr="current_stays",
        )
    )
    filterset_fields = ["is_active", "status", "room", "room__department"]

    @action(detail=False, methods=["get"])
    def board(self, request):
        """Tableau d'occupation : tous les lits actifs des chambres actives, non paginé."""
        beds = self.filter_queryset(self.get_queryset()).filter(is_active=True, room__is_active=True).order_by(
            "room__department__name", "room__number", "label"
        )
        return Response(self.get_serializer(beds, many=True).data)

    def _transition(self, func, **kwargs):
        bed = _service_call(func, bed=self.get_object(), actor=self.request.user, **kwargs)
        return Response(self.get_serializer(self.get_queryset().get(pk=bed.pk)).data)

    @action(detail=True, methods=["post"])
    def clean(self, request, pk=None):
        return self._transition(services.mark_bed_clean)

    @action(detail=True, methods=["post"], url_path="out-of-service")
    def out_of_service(self, request, pk=None):
        return self._transition(services.set_bed_out_of_service)

    @action(detail=True, methods=["post"], url_path="back-in-service")
    def back_in_service(self, request, pk=None):
        return self._transition(services.put_bed_back_in_service)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self._transition(services.set_bed_active, active=False)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        return self._transition(services.set_bed_active, active=True)


class AdmissionFilter(filters.FilterSet):
    status = filters.MultipleChoiceFilter(choices=Admission.Status.choices)

    class Meta:
        model = Admission
        fields = ["status", "department", "patient", "doctor"]


class AdmissionViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Séjours (docs/hospitalization.md §5). Tous les séjours de la clinique sont visibles du
    personnel autorisé — un médecin de garde ou un autre médecin de la clinique peut prononcer la
    sortie (permissions-matrix.md) — mais chaque rôle n'en reçoit que les champs permis."""

    serializer_class = AdmissionSerializer
    permission_classes = TenantScopedMixin.permission_classes + [CanAccessAdmissions]
    queryset = Admission.objects.select_related(
        "patient", "doctor__user", "department", "bed__room", "admitted_by", "discharged_by", "invoice"
    ).prefetch_related(
        Prefetch("transfers", queryset=BedTransfer.objects.select_related("from_bed__room", "to_bed__room", "transferred_by"))
    )
    filter_backends = [filters.DjangoFilterBackend, SearchFilter]
    filterset_class = AdmissionFilter
    search_fields = ["number", "patient__first_name", "patient__last_name", "patient__patient_number"]

    def _doctor_profile(self):
        doctor = getattr(self.request.user, "doctor_profile", None)
        if doctor is None:
            raise PermissionDenied(_("Ce compte n'a pas de profil médecin dans cette clinique."))
        return doctor

    def _fresh(self, admission):
        return self.get_serializer(self.get_queryset().get(pk=admission.pk)).data

    def retrieve(self, request, *args, **kwargs):
        admission = self.get_object()
        record_audit(user=request.user, action=AuditLog.Action.VIEW, obj=admission)
        return Response(self.get_serializer(admission).data)

    def create(self, request, *args, **kwargs):
        doctor = self._doctor_profile()
        payload = AdmissionCreateSerializer(data=request.data, context={"request": request})
        payload.is_valid(raise_exception=True)
        admission = _service_call(
            services.create_admission, clinic=request.user.clinic, doctor=doctor, actor=request.user,
            **payload.validated_data,
        )
        return Response(self._fresh(admission), status=201)

    def _bed_choice(self, request):
        payload = BedChoiceSerializer(data=request.data, context={"request": request})
        payload.is_valid(raise_exception=True)
        return payload.validated_data

    @action(detail=True, methods=["post"])
    def admit(self, request, pk=None):
        self._doctor_profile()
        choice = self._bed_choice(request)
        admission = _service_call(services.admit, admission=self.get_object(), bed=choice["bed"], actor=request.user)
        return Response(self._fresh(admission))

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        choice = self._bed_choice(request)
        admission = _service_call(
            services.transfer, admission=self.get_object(), bed=choice["bed"], reason=choice["reason"], actor=request.user,
        )
        return Response(self._fresh(admission))

    @action(detail=True, methods=["post"])
    def discharge(self, request, pk=None):
        self._doctor_profile()
        payload = DischargeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        admission = _service_call(
            services.discharge, admission=self.get_object(), actor=request.user, summary=payload.validated_data["summary"],
        )
        return Response(self._fresh(admission))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        admission = _service_call(services.cancel_admission, admission=self.get_object(), actor=request.user)
        return Response(self._fresh(admission))

    @action(detail=True, methods=["get", "post"])
    def vitals(self, request, pk=None):
        admission = self.get_object()
        if request.method == "POST":
            payload = VitalSignSerializer(data=request.data)
            payload.is_valid(raise_exception=True)
            vital = _service_call(services.record_vital_signs, admission=admission, actor=request.user, **payload.validated_data)
            return Response(VitalSignSerializer(vital).data, status=201)
        vitals = admission.vital_signs.select_related("recorded_by")
        return Response(VitalSignSerializer(vitals, many=True).data)

    @action(detail=True, methods=["get", "post"])
    def notes(self, request, pk=None):
        admission = self.get_object()
        if request.method == "POST":
            payload = NursingNoteSerializer(data=request.data)
            payload.is_valid(raise_exception=True)
            note = _service_call(services.add_nursing_note, admission=admission, actor=request.user, note=payload.validated_data["note"])
            return Response(NursingNoteSerializer(note).data, status=201)
        notes = admission.nursing_notes.select_related("recorded_by")
        return Response(NursingNoteSerializer(notes, many=True).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        admission = self.get_object()
        if admission.status != Admission.Status.DISCHARGED:
            raise serializers.ValidationError(_("Le résumé de séjour n'est disponible qu'après la sortie."))
        response = HttpResponse(render_stay_summary_pdf(admission=admission, user=request.user), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{admission.number}.pdf"'
        return response
