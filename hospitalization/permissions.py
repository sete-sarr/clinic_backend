from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role

# Rôles par action (business/permissions-matrix.md § CHAMBRES ET LITS, HOSPITALISATION, CONSTANTES
# ET NOTES DE SOINS). La lecture est ensuite filtrée champ par champ par les serializers
# (business/access-policy.md : réception = emplacement uniquement, comptabilité = dates et nuitées).
CLINICAL = ("doctor", "nurse", "clinic_admin")

STRUCTURE_ACTION_ROLES = {
    "list": ("doctor", "nurse", "secretary", "clinic_admin"),
    "retrieve": ("doctor", "nurse", "secretary", "clinic_admin"),
    "board": ("doctor", "nurse", "secretary", "clinic_admin"),
    "clean": ("nurse", "clinic_admin"),
}

ADMISSION_ACTION_ROLES = {
    "list": ("doctor", "nurse", "secretary", "accountant", "clinic_admin"),
    "retrieve": ("doctor", "nurse", "secretary", "accountant", "clinic_admin"),
    "create": ("doctor",),
    "admit": ("doctor",),
    "transfer": ("doctor", "nurse"),
    "discharge": ("doctor",),
    "cancel": ("doctor", "clinic_admin"),
    "pdf": CLINICAL,
}

# Constantes et notes de soins : lecture clinique, écriture infirmier / médecin.
CARE_ROLES = {"GET": CLINICAL, "POST": ("doctor", "nurse")}


class CanManageWardStructure(BasePermission):
    """Types de chambre, chambres et lits : écriture réservée à l'administrateur."""

    def has_permission(self, request, view):
        roles = STRUCTURE_ACTION_ROLES.get(view.action)
        if roles is None:
            roles = STRUCTURE_ACTION_ROLES["list"] if request.method in SAFE_METHODS else ("clinic_admin",)
        return in_role(request.user, *roles)


class CanAccessAdmissions(BasePermission):
    def has_permission(self, request, view):
        if view.action in ("vitals", "notes"):
            return in_role(request.user, *CARE_ROLES.get(request.method, ()))
        roles = ADMISSION_ACTION_ROLES.get(view.action)
        return bool(roles) and in_role(request.user, *roles)
