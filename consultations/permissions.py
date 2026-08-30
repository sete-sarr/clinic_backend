from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanManageConsultations(BasePermission):
    """business-rules.md: only doctors create/edit consultations; a doctor sees only their own."""

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return in_role(user, "doctor", "clinic_admin", "patient")
        return in_role(user, "doctor", "clinic_admin")

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or in_role(user, "clinic_admin"):
            return True
        if in_role(user, "doctor"):
            return getattr(obj.doctor, "user_id", None) == user.id
        if request.method in SAFE_METHODS and in_role(user, "patient"):
            return getattr(obj.patient, "user_id", None) == user.id
        return False
