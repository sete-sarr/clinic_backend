from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanAccessMedicalRecord(BasePermission):
    """
    business/access-policy.md: medical record content is restricted to treating
    doctors (read/write) and the patient concerned (read-only). Front-desk /
    billing roles never see clinical content.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if in_role(user, "doctor"):
            return True
        if in_role(user, "patient"):
            return request.method in SAFE_METHODS
        return user.is_superuser

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or in_role(user, "doctor"):
            return True
        if in_role(user, "patient") and request.method in SAFE_METHODS:
            return getattr(obj.patient, "user_id", None) == user.id
        return False
