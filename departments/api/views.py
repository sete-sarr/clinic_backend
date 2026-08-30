from django.core.exceptions import ValidationError as DjangoValidationError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.response import Response

from common.audit import record_audit
from common.models import AuditLog
from common.viewsets import TenantScopedModelViewSet
from departments.models import Department
from departments.permissions import CanManageDepartments
from departments.services import activate_department, archive_department, deactivate_department, restore_department

from .serializers import DepartmentSerializer


class DepartmentViewSet(TenantScopedModelViewSet):
    serializer_class = DepartmentSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageDepartments]
    queryset = Department.objects.select_related("clinic").all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["is_active", "status", "department_type"]
    http_method_names = ["get", "post", "patch", "head", "options"]  # no PUT/DELETE — see archive()

    def perform_create(self, serializer):
        department = serializer.save(clinic=self.request.user.clinic)
        record_audit(user=self.request.user, action=AuditLog.Action.CREATE, obj=department)

    def perform_update(self, serializer):
        department = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=department)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        # business/validation-rules.md: physical deletion is forbidden and archiving is blocked
        # while active doctors are assigned — replaces the generic DELETE verb (see
        # http_method_names) so that guard can never be bypassed.
        department = self.get_object()
        try:
            archive_department(department=department, actor=request.user)
        except DjangoValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return Response({"code": 400, "message": message, "field": None}, status=400)
        return Response(DepartmentSerializer(department).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        department = self.get_object()
        restore_department(department=department, actor=request.user)
        return Response(DepartmentSerializer(department).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        department = self.get_object()
        try:
            deactivate_department(department=department, actor=request.user)
        except DjangoValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return Response({"code": 400, "message": message, "field": None}, status=400)
        return Response(DepartmentSerializer(department).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        department = self.get_object()
        try:
            activate_department(department=department, actor=request.user)
        except DjangoValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return Response({"code": 400, "message": message, "field": None}, status=400)
        return Response(DepartmentSerializer(department).data)
