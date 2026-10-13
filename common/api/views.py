from django.http import Http404, HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from common.models import AuditLog
from common.permissions import IsClinicAdmin
from common.photos import PHOTO_CONTENT_TYPE, photo_from_token, read_photo
from common.viewsets import TenantScopedMixin

from .serializers import AuditLogSerializer


class PhotoView(APIView):
    """Photo de profil à partir de son URL signée (common/photos.py::photo_url), renvoyée
    uniquement aux utilisateurs autorisés à voir la fiche de la personne. L'image est lue dans le
    bucket privé : celui-ci n'est jamais exposé. Sans authentification (une balise <img> n'envoie pas
    le JWT) : la signature, limitée dans le temps, protège. Cache privé jusqu'à l'expiration de l'URL."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        photo, remaining = photo_from_token(token)
        content = read_photo(photo) if photo is not None else None
        if content is None:
            raise Http404
        response = HttpResponse(content, content_type=PHOTO_CONTENT_TYPE)
        response["Cache-Control"] = f"private, max-age={remaining}"
        return response


class AuditLogViewSet(TenantScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only — audit entries are written exclusively via common.audit.record_audit(), never
    through this API. business/access-policy.md: "Clinic Administrator: Can access ... All Audit
    Logs" — no other role is granted access here."""

    serializer_class = AuditLogSerializer
    permission_classes = TenantScopedMixin.permission_classes + [IsClinicAdmin]
    queryset = AuditLog.objects.select_related("user", "clinic").all()
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = {
        "action": ["exact"],
        "model_name": ["exact"],
        "created_at": ["gte", "lte"],
    }
    search_fields = ["model_name", "object_id"]


class DailyTasksView(APIView):
    """POST /api/v1/internal/daily-tasks/ — tâches quotidiennes (common/daily_tasks.py), déclenchées
    par GitHub Actions. Sans compte utilisateur : protégé par le secret partagé CRON_SECRET, envoyé
    dans l'en-tête « Authorization: Bearer <secret> » et comparé en temps constant. Introuvable (404)
    tant que CRON_SECRET n'est pas configuré."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def post(self, request):
        import hmac

        from django.conf import settings
        from rest_framework.response import Response

        from common.daily_tasks import run_daily_tasks

        secret = settings.CRON_SECRET
        if not secret:
            raise Http404
        supplied = request.META.get("HTTP_AUTHORIZATION", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), secret.encode()):
            return Response({"detail": "Forbidden"}, status=403)
        report = run_daily_tasks()
        status = 200 if all(entry["ok"] for entry in report.values()) else 500
        return Response(report, status=status)
