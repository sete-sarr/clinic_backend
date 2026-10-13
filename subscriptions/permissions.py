from django.utils.translation import gettext_lazy
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .catalog import has_feature


def requires_plan_feature(feature):
    """Permission DRF : création et modification réservées aux cliniques dont la formule (ou l'essai)
    comprend `feature` (subscriptions/catalog.py). La lecture reste ouverte : une clinique repassée
    en Starter garde l'accès à ses données existantes. S'ajoute aux contrôles de rôle et de tenant,
    jamais à leur place."""

    class PlanFeaturePermission(BasePermission):
        message = gettext_lazy(
            "Ce module fait partie de la formule Professional. Changez de formule depuis l'écran « Abonnement »."
        )

        def has_permission(self, request, view):
            if request.method in SAFE_METHODS:
                return True
            user = request.user
            clinic = getattr(user, "clinic", None)
            if not user.is_authenticated or user.is_superuser or clinic is None:
                return True
            return has_feature(clinic, feature)

    PlanFeaturePermission.__name__ = f"PlanFeaturePermission_{feature}"
    return PlanFeaturePermission
