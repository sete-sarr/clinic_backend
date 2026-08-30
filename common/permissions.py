from rest_framework.permissions import BasePermission

STAFF_ROLES = ["doctor", "secretary", "accountant", "clinic_admin"]


def in_role(user, *role_names):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=role_names).exists()


def _role_permission(*role_names):
    class _RolePermission(BasePermission):
        def has_permission(self, request, view):
            return in_role(request.user, *role_names)

    return _RolePermission


IsDoctor = _role_permission("doctor")
IsSecretary = _role_permission("secretary")
IsAccountant = _role_permission("accountant")
IsClinicAdmin = _role_permission("clinic_admin")
IsPatient = _role_permission("patient")
IsStaff = _role_permission(*STAFF_ROLES)


class IsSameClinic(BasePermission):
    """Object-level defense in depth on top of queryset scoping (docs/security.md)."""

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser:
            return True
        obj_clinic_id = getattr(obj, "clinic_id", None)
        return bool(user.clinic_id) and obj_clinic_id == user.clinic_id
