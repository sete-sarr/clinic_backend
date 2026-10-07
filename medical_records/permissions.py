from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanAccessMedicalRecord(BasePermission):
    """
    business/access-policy.md: medical record content is restricted to treating
    doctors (read/write) and the patient concerned (read-only). Front-desk /
    billing roles never see clinical content.

    Infirmier (Phase 5.2, access-policy.md § INFIRMIER) : lecture seule, uniquement pour les
    patients actuellement hospitalisés (périmètre appliqué aussi par le queryset).
    """

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if in_role(user, "doctor"):
            return True
        if in_role(user, "patient", "nurse"):
            return request.method in SAFE_METHODS
        return user.is_superuser

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or in_role(user, "doctor"):
            return True
        if in_role(user, "patient") and request.method in SAFE_METHODS:
            return getattr(obj.patient, "user_id", None) == user.id
        if in_role(user, "nurse") and request.method in SAFE_METHODS:
            from hospitalization.services import admitted_patient_ids

            return admitted_patient_ids(obj.clinic_id).filter(patient_id=obj.patient_id).exists()
        return False
