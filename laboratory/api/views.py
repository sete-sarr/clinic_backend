from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.utils.translation import gettext as _
from django_filters import rest_framework as filters
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from common.audit import record_audit
from common.models import AuditLog
from common.permissions import in_role
from common.viewsets import TenantScopedMixin, TenantScopedModelViewSet
from hospitalization.services import admitted_patient_ids
from laboratory import services
from laboratory.models import LabOrder, LabResult, LabResultFile, LabTest
from laboratory.pdf import render_lab_results_pdf
from laboratory.permissions import CanAccessLabOrders, CanManageLabTests

from .serializers import (
    AttachmentSerializer,
    CorrectResultSerializer,
    LabOrderCreateSerializer,
    LabOrderSerializer,
    LabTestSerializer,
    RecordResultsSerializer,
)


def _service_call(func, **kwargs):
    try:
        return func(**kwargs)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.messages) from exc


class LabTestViewSet(TenantScopedModelViewSet):
    serializer_class = LabTestSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageLabTests]
    queryset = LabTest.objects.all()
    filter_backends = [filters.DjangoFilterBackend, SearchFilter]
    filterset_fields = ["is_active"]
    search_fields = ["code", "name"]
    http_method_names = ["get", "post", "patch", "head", "options"]  # pas de suppression : archive()

    def perform_create(self, serializer):
        serializer.instance = _service_call(
            services.create_lab_test, clinic=self.request.user.clinic, actor=self.request.user, **serializer.validated_data
        )

    def perform_update(self, serializer):
        serializer.instance = _service_call(
            services.update_lab_test, test=serializer.instance, actor=self.request.user, **serializer.validated_data
        )

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        test = services.set_lab_test_active(test=self.get_object(), actor=request.user, active=False)
        return Response(self.get_serializer(test).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        test = services.set_lab_test_active(test=self.get_object(), actor=request.user, active=True)
        return Response(self.get_serializer(test).data)


class LabOrderFilter(filters.FilterSet):
    status = filters.MultipleChoiceFilter(choices=LabOrder.Status.choices)

    class Meta:
        model = LabOrder
        fields = ["status", "patient", "doctor"]


class LabOrderViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Demandes d'examens (docs/laboratory.md §5). Aucune modification libre : chaque changement
    passe par une action, donc par la machine à états de laboratory/services.py.

    Périmètre par rôle (business/access-policy.md) : administrateur et technicien — toutes les
    demandes de la clinique ; médecin — ses demandes ; patient — ses demandes validées."""

    serializer_class = LabOrderSerializer
    permission_classes = TenantScopedMixin.permission_classes + [CanAccessLabOrders]
    queryset = LabOrder.objects.select_related("patient", "doctor__user", "validated_by", "invoice").prefetch_related(
        "items",
        Prefetch(
            "items__results",
            queryset=LabResult.objects.select_related("entered_by", "attachment").defer("attachment__content"),
        ),
    )
    filter_backends = [filters.DjangoFilterBackend, SearchFilter]
    filterset_class = LabOrderFilter
    search_fields = ["number", "patient__first_name", "patient__last_name", "patient__patient_number"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser or in_role(user, "clinic_admin", "lab_technician"):
            return qs
        scope = Q(pk__in=[])
        doctor_profile = getattr(user, "doctor_profile", None)
        if doctor_profile and in_role(user, "doctor"):
            scope |= Q(doctor=doctor_profile)
        patient_profile = getattr(user, "patient_profile", None)
        if patient_profile and in_role(user, "patient"):
            scope |= Q(patient=patient_profile, status=LabOrder.Status.VALIDATED)
        if in_role(user, "nurse"):
            # Résultats validés des patients hospitalisés (access-policy.md § INFIRMIER).
            scope |= Q(patient_id__in=admitted_patient_ids(user.clinic_id), status=LabOrder.Status.VALIDATED)
        return qs.filter(scope)

    def retrieve(self, request, *args, **kwargs):
        order = self.get_object()
        # Résultat d'examen = donnée de santé : chaque consultation est auditée (docs/laboratory.md §8).
        record_audit(user=request.user, action=AuditLog.Action.VIEW, obj=order)
        return Response(self.get_serializer(order).data)

    def create(self, request, *args, **kwargs):
        doctor_profile = getattr(request.user, "doctor_profile", None)
        if doctor_profile is None:
            raise PermissionDenied(_("Ce compte n'a pas de profil médecin dans cette clinique."))
        payload = LabOrderCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        order = _service_call(
            services.create_lab_order, clinic=request.user.clinic, doctor=doctor_profile, actor=request.user,
            **payload.validated_data,
        )
        return Response(self._fresh(order), status=201)

    def _fresh(self, order):
        return self.get_serializer(self.get_queryset().get(pk=order.pk)).data

    def _transition(self, func, request, **kwargs):
        order = _service_call(func, order=self.get_object(), actor=request.user, **kwargs)
        return Response(self._fresh(order))

    @action(detail=True, methods=["post"])
    def collect(self, request, pk=None):
        return self._transition(services.collect_sample, request)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._transition(services.start_processing, request)

    @action(detail=True, methods=["post"])
    def results(self, request, pk=None):
        payload = RecordResultsSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._transition(services.record_results, request, entries=payload.validated_data["results"])

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._transition(services.complete_order, request)

    @action(detail=True, methods=["post"])
    def validate(self, request, pk=None):
        return self._transition(services.validate_order, request)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._transition(services.cancel_order, request)

    @action(detail=True, methods=["post"], url_path=r"items/(?P<item_id>\d+)/correct")
    def correct(self, request, pk=None, item_id=None):
        payload = CorrectResultSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._transition(services.correct_result, request, item_id=int(item_id), **payload.validated_data)

    @action(
        detail=True, methods=["get", "post"], url_path=r"items/(?P<item_id>\d+)/attachment",
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def attachment(self, request, pk=None, item_id=None):
        order = self.get_object()
        if request.method == "POST":
            payload = AttachmentSerializer(data=request.data)
            payload.is_valid(raise_exception=True)
            return self._transition(
                services.attach_result_file, request, item_id=int(item_id), uploaded_file=payload.validated_data["file"]
            )
        attachment = (
            LabResultFile.objects.filter(result__item__order=order, result__item_id=item_id, result__superseded_at__isnull=True)
            .select_related("result__item")
            .first()
        )
        if attachment is None:
            raise Http404
        record_audit(user=request.user, action=AuditLog.Action.VIEW, obj=order, metadata={"attachment": attachment.result.item.test_code})
        response = HttpResponse(bytes(attachment.content), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{order.number}-{attachment.result.item.test_code}.pdf"'
        return response

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        order = self.get_object()
        if order.status != LabOrder.Status.VALIDATED:
            raise serializers.ValidationError(_("Le compte rendu n'est disponible qu'après validation des résultats."))
        pdf_bytes = render_lab_results_pdf(order=order, user=request.user)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{order.number}.pdf"'
        return response
