from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanManagePayments(BasePermission):
    """business/permissions-matrix.md: payments created by cashier (accountant); update forbidden after validation."""

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return in_role(user, "accountant", "clinic_admin", "secretary", "doctor", "patient")
        return in_role(user, "accountant", "clinic_admin")

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or in_role(user, "clinic_admin", "accountant"):
            return True
        if request.method in SAFE_METHODS:
            if in_role(user, "secretary", "doctor"):
                return True
            if in_role(user, "patient"):
                return getattr(obj.invoice.patient, "user_id", None) == user.id
        return False
