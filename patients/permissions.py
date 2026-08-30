from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanManagePatients(BasePermission):
    """
    business/permissions-matrix.md: patients are created/updated by
    receptionist (secretary) or admin roles; soft-delete is admin-only.
    Any clinic staff role may read. A patient may read their own record
    (business/access-policy.md: patient "Can access: Own Profile") — the
    corresponding queryset scoping lives in PatientViewSet.get_queryset().
    """

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return in_role(user, "doctor", "secretary", "accountant", "clinic_admin", "patient")
        if request.method == "DELETE":
            return in_role(user, "clinic_admin")
        return in_role(user, "secretary", "clinic_admin")
