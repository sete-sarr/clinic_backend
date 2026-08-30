from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanManageDepartments(BasePermission):
    """Any authenticated clinic staff can read; only a clinic admin can write."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        return in_role(request.user, "clinic_admin")
