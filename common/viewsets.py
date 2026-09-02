from django.utils import timezone
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .permissions import IsSameClinic, SubscriptionActivePermission


class TenantScopedMixin:
    """
    Enforces row-level multi-tenant isolation (docs/architecture.md).

    Subclasses must not override get_queryset()/perform_create() without
    calling super() — every business queryset must stay filtered by the
    request user's clinic, no exceptions.
    """

    permission_classes = [IsAuthenticated, IsSameClinic, SubscriptionActivePermission]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            return qs
        if not getattr(user, "clinic_id", None):
            return qs.none()
        return qs.filter(clinic_id=user.clinic_id)

    def perform_create(self, serializer):
        serializer.save(clinic=self.request.user.clinic)


class TenantScopedModelViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """
    Full CRUD tenant-scoped viewset, with soft delete (business/validation-rules.md,
    permissions-matrix.md: physical deletion of business entities is forbidden
    system-wide). Use TenantScopedMixin directly for resources that must not
    expose update/delete (e.g. payments).
    """

    def perform_destroy(self, instance):
        if hasattr(instance, "is_active"):
            instance.is_active = False
            if hasattr(instance, "archived_at"):
                instance.archived_at = timezone.now()
            instance.save()
        else:
            instance.delete()
