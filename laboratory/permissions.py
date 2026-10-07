from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanManageLabTests(BasePermission):
    """Catalogue d'examens (permissions-matrix.md § EXAMEN DE LABORATOIRE) : consultation par le
    médecin, le technicien de laboratoire et l'administrateur ; écriture par l'administrateur."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return in_role(request.user, "doctor", "lab_technician", "clinic_admin")
        return in_role(request.user, "clinic_admin")


# Rôles autorisés par action de LabOrderViewSet (permissions-matrix.md § DEMANDE / RÉSULTAT DE
# LABORATOIRE). La lecture est ouverte à ces rôles ; le périmètre (ses demandes, ses résultats
# validés…) est appliqué par le queryset.
ORDER_ACTION_ROLES = {
    "list": ("doctor", "lab_technician", "clinic_admin", "patient", "nurse"),
    "retrieve": ("doctor", "lab_technician", "clinic_admin", "patient", "nurse"),
    "attachment": ("doctor", "lab_technician", "clinic_admin", "patient", "nurse"),
    "pdf": ("doctor", "clinic_admin", "patient"),
    "create": ("doctor",),
    "collect": ("lab_technician",),
    "start": ("lab_technician",),
    "results": ("lab_technician",),
    "complete": ("lab_technician",),
    "correct": ("lab_technician",),
    "validate": ("doctor",),
    "cancel": ("doctor", "clinic_admin"),
}


class CanAccessLabOrders(BasePermission):
    def has_permission(self, request, view):
        action = view.action
        if action == "attachment" and request.method not in SAFE_METHODS:
            return in_role(request.user, "lab_technician")
        roles = ORDER_ACTION_ROLES.get(action)
        return bool(roles) and in_role(request.user, *roles)
