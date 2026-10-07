from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanManagePrescriptions(BasePermission):
    """business-rules.md: prescribing doctor, the patient concerned, or a clinic admin may access."""

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            # nurse : prescriptions en cours des patients hospitalisés (PrescriptionViewSet.get_queryset).
            return in_role(user, "doctor", "clinic_admin", "patient", "nurse")
        return in_role(user, "doctor", "clinic_admin")

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or in_role(user, "clinic_admin"):
            return True
        if in_role(user, "doctor"):
            return getattr(obj.doctor, "user_id", None) == user.id
        if request.method in SAFE_METHODS and in_role(user, "patient"):
            return getattr(obj.patient, "user_id", None) == user.id
        if request.method in SAFE_METHODS and in_role(user, "nurse"):
            from hospitalization.services import admitted_patient_ids

            return obj.status == "validated" and admitted_patient_ids(obj.clinic_id).filter(patient_id=obj.patient_id).exists()
        return False
