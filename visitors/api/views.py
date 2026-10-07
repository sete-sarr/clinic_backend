from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django_filters import rest_framework as filters
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from common.exports import CsvExportMixin
from common.permissions import in_role
from common.viewsets import TenantScopedMixin
from hospitalization.models import Admission
from visitors import services
from visitors.models import VisitLog

from .serializers import VisitLogSerializer, admission_location

TARGET_LIMIT = 10


class CanUseVisitorRegistry(BasePermission):
    """permissions-matrix.md § REGISTRE DES VISITEURS : réception et administrateur ; l'export CSV
    est réservé à l'administrateur (audité par CsvExportMixin)."""

    def has_permission(self, request, view):
        if view.action == "export_csv":
            return in_role(request.user, "clinic_admin")
        return in_role(request.user, "secretary", "clinic_admin")


class VisitLogFilter(filters.FilterSet):
    present = filters.BooleanFilter(field_name="checked_out_at", lookup_expr="isnull")
    date_from = filters.DateFilter(field_name="checked_in_at", lookup_expr="date__gte")
    date_to = filters.DateFilter(field_name="checked_in_at", lookup_expr="date__lte")

    class Meta:
        model = VisitLog
        fields = ["visitor_type", "visited_admission", "visited_staff"]


class VisitLogViewSet(
    CsvExportMixin,
    TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Registre des visiteurs (docs/visitors.md §5). Pas de suppression : seule la purge
    automatique après 1 an supprime des entrées."""

    serializer_class = VisitLogSerializer
    permission_classes = TenantScopedMixin.permission_classes + [CanUseVisitorRegistry]
    queryset = VisitLog.objects.select_related(
        "checked_in_by", "checked_out_by", "visited_staff",
        "visited_admission__patient", "visited_admission__department", "visited_admission__bed__room",
    )
    filter_backends = [filters.DjangoFilterBackend, SearchFilter]
    filterset_class = VisitLogFilter
    search_fields = ["visitor_name", "visitor_phone", "purpose", "visited_free_text"]
    http_method_names = ["get", "post", "patch", "head", "options"]
    export_fields = [
        "visitor_name", "visitor_phone", "visitor_type", "purpose", "visited_display",
        "checked_in_at", "checked_in_by_display", "checked_out_at", "checked_out_by_display", "auto_closed",
    ]

    def _call(self, func, **kwargs):
        try:
            return func(**kwargs)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

    def perform_create(self, serializer):
        serializer.instance = self._call(
            services.check_in, clinic=self.request.user.clinic, actor=self.request.user, **serializer.validated_data
        )

    def perform_update(self, serializer):
        serializer.instance = self._call(
            services.update_visit, visit=serializer.instance, actor=self.request.user, **serializer.validated_data
        )

    @action(detail=True, methods=["post"], url_path="check-out")
    def check_out(self, request, pk=None):
        visit = self._call(services.check_out, visit=self.get_object(), actor=request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=visit.pk)).data)

    @action(detail=False, methods=["get"])
    def targets(self, request):
        """Personnes visitables : patients hospitalisés (nom + emplacement, jamais le motif) et
        membres du personnel actifs de la clinique."""
        search = request.query_params.get("search", "").strip()
        clinic_id = request.user.clinic_id
        admissions = Admission.objects.filter(clinic_id=clinic_id, status=Admission.Status.ADMITTED).select_related(
            "patient", "department", "bed__room"
        )
        staff = get_user_model().objects.filter(clinic_id=clinic_id, is_active=True).exclude(groups__name="patient")
        if search:
            admissions = admissions.filter(Q(patient__first_name__icontains=search) | Q(patient__last_name__icontains=search))
            staff = staff.filter(Q(first_name__icontains=search) | Q(last_name__icontains=search))
        return Response({
            "admissions": [{"id": a.id, "label": admission_location(a)} for a in admissions[:TARGET_LIMIT]],
            "staff": [{"id": u.id, "label": u.get_full_name() or u.get_username()} for u in staff.distinct()[:TARGET_LIMIT]],
        })
