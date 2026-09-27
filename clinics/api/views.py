from pathlib import Path

from django.http import FileResponse, Http404
from rest_framework import generics, mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog
from common.permissions import IsClinicAdmin

from .serializers import ClinicPublicSerializer, ClinicSerializer


class ClinicViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Clinic is the tenant itself: staff only ever see their own clinic, and only
    a clinic admin (or platform superuser) may edit it. No create/delete here —
    provisioning a new tenant is a platform-level operation, not a business one.
    """

    serializer_class = ClinicSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return Clinic.objects.all()
        if user.clinic_id:
            return Clinic.objects.filter(pk=user.clinic_id)
        return Clinic.objects.none()

    def get_permissions(self):
        if self.action in ("update", "partial_update"):
            return [IsAuthenticated(), IsClinicAdmin()]
        return super().get_permissions()

    def perform_update(self, serializer):
        clinic = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=clinic)


class ClinicPublicListView(generics.ListAPIView):
    """Pre-auth clinic picker for patient account activation (features/patient-portal/activation).
    id/name only — see ClinicPublicSerializer for why this must never reuse ClinicSerializer."""

    serializer_class = ClinicPublicSerializer
    permission_classes = [AllowAny]
    queryset = Clinic.objects.filter(is_active=True)
    filter_backends = [SearchFilter]
    search_fields = ["name"]


# Copie publiée de guide-utilisateur/Guide-de-la-Clinique.pdf (source HTML dans ce dossier) —
# volontairement hors de STATIC_ROOT/MEDIA_ROOT pour ne jamais être servie sans authentification.
USER_GUIDE_PATH = Path(__file__).resolve().parent.parent / "resources" / "Guide-de-la-Clinique.pdf"
USER_GUIDE_FILENAME = "Guide-de-la-Clinique.pdf"


class UserGuideDownloadView(APIView):
    """Guide d'utilisation de la plateforme, réservé à l'administrateur de clinique (décision
    produit, session du 2026-09-28). Document générique, sans donnée de clinique ni de patient :
    aucun scoping tenant nécessaire au-delà du contrôle de rôle."""

    permission_classes = [IsAuthenticated, IsClinicAdmin]

    def get(self, request):
        if not USER_GUIDE_PATH.is_file():
            raise Http404("Le guide d'utilisation n'est pas disponible.")
        return FileResponse(
            USER_GUIDE_PATH.open("rb"),
            as_attachment=True,
            filename=USER_GUIDE_FILENAME,
            content_type="application/pdf",
        )
