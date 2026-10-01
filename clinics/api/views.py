from pathlib import Path

from django.http import FileResponse, Http404, HttpResponse
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from rest_framework import generics, mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from clinics.models import Clinic
from clinics.services import logo_from_token
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


class ClinicLogoView(APIView):
    """Logo d'une clinique à partir de son URL signée (clinics/services.py::logo_url), renvoyée
    uniquement aux utilisateurs authentifiés de la clinique par ClinicSerializer. Sans
    authentification (une balise <img> n'envoie pas le JWT) : c'est la signature qui protège,
    pas l'identifiant de la clinique. L'URL change à chaque nouveau logo, d'où le cache long."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        logo = logo_from_token(token)
        if logo is None:
            raise Http404
        response = HttpResponse(bytes(logo.content), content_type=logo.content_type)
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# Copies publiées des guides de guide-utilisateur/ (source HTML dans ce dossier), une par langue de
# l'interface (docs/i18n.md) — volontairement hors de STATIC_ROOT/MEDIA_ROOT pour ne jamais être
# servies sans authentification.
USER_GUIDES_DIR = Path(__file__).resolve().parent.parent / "resources"
USER_GUIDE_FILENAMES = {"fr": "Guide-de-la-Clinique.pdf", "en": "Clinic-User-Guide.pdf"}


def user_guide_filename(language):
    """Guide dans la langue de l'interface (en-tête Accept-Language) ; français par défaut."""
    return USER_GUIDE_FILENAMES.get((language or "")[:2], USER_GUIDE_FILENAMES["fr"])


class UserGuideDownloadView(APIView):
    """Guide d'utilisation de la plateforme, réservé à l'administrateur de clinique (décision
    produit, session du 2026-09-28), dans la langue d'affichage de l'utilisateur. Document
    générique, sans donnée de clinique ni de patient : aucun scoping tenant nécessaire au-delà du
    contrôle de rôle."""

    permission_classes = [IsAuthenticated, IsClinicAdmin]

    def get(self, request):
        filename = user_guide_filename(get_language())
        path = USER_GUIDES_DIR / filename
        if not path.is_file():
            raise Http404(_("Le guide d'utilisation n'est pas disponible."))
        return FileResponse(path.open("rb"), as_attachment=True, filename=filename, content_type="application/pdf")
