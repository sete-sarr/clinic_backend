from rest_framework.permissions import SAFE_METHODS, BasePermission

from common.permissions import in_role


class CanManageMedications(BasePermission):
    """Catalogue de médicaments : tout le personnel authentifié peut consulter (docs/security.md
    "doctor, secretary, accountant, clinic admin" + pharmacist) ; seuls le pharmacien et
    l'administrateur de clinique peuvent créer/modifier/archiver (décision produit, session du
    2026-09-16 : « Pharmacien + Administrateur de clinique »)."""

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return in_role(user, "doctor", "secretary", "accountant", "clinic_admin", "pharmacist")
        return in_role(user, "pharmacist", "clinic_admin")


class CanManageStock(BasePermission):
    """Réception de lot (achat) et ajustement manuel — même règle que CanManageMedications pour
    l'écriture ; la lecture du ledger (StockMovement) reste ouverte à tout le personnel."""

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return in_role(user, "doctor", "secretary", "accountant", "clinic_admin", "pharmacist")
        return in_role(user, "pharmacist", "clinic_admin")
