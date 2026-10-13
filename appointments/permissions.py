from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanManageAppointments(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return in_role(user, "doctor", "secretary", "accountant", "clinic_admin", "patient")
        # Un patient ne peut que créer sa propre réservation ou l'annuler — jamais via le chemin
        # générique PUT/PATCH/DELETE (business/permissions-matrix.md ne liste Patient que sous Create).
        if request.method == "POST" and getattr(view, "action", None) in ("create", "cancel"):
            return in_role(user, "doctor", "secretary", "clinic_admin", "patient")
        # Rendez-vous de département : un médecin le prend en charge, l'accueil l'attribue.
        if getattr(view, "action", None) == "take":
            return in_role(user, "doctor")
        if getattr(view, "action", None) == "assign":
            return in_role(user, "secretary", "clinic_admin")
        return in_role(user, "doctor", "secretary", "clinic_admin")

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or in_role(user, "clinic_admin", "secretary"):
            return True
        if in_role(user, "doctor"):
            # business-rules.md : un médecin ne peut créer/modifier que ses propres rendez-vous.
            if getattr(obj.doctor, "user_id", None) == user.id:
                return True
            # Rendez-vous de son département encore à attribuer : consultation et prise en charge
            # uniquement (get_queryset ne lui montre que ceux-là).
            return obj.doctor_id is None and (request.method in SAFE_METHODS or view.action == "take")
        if in_role(user, "patient"):
            if request.method in SAFE_METHODS or getattr(view, "action", None) == "cancel":
                return getattr(obj.patient, "user_id", None) == user.id
            return False
        return False
