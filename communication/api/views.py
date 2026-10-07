from django.utils import timezone
from django_filters import rest_framework as filters
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from communication.models import InAppNotification

from .serializers import InAppNotificationSerializer


class InAppNotificationFilter(filters.FilterSet):
    unread = filters.BooleanFilter(field_name="read_at", lookup_expr="isnull")
    archived = filters.BooleanFilter(field_name="archived_at", lookup_expr="isnull", exclude=True)

    class Meta:
        model = InAppNotification
        fields = ["category", "priority"]


class InAppNotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Centre de notifications in-app (docs/communication-architecture.md) : chaque utilisateur ne
    voit que SES notifications, de SA clinique — le scoping par destinataire remplace le scoping par
    rôle. Lecture seule hormis les marqueurs lu / archivé ; les notifications sont créées uniquement
    par communication.services.notify_in_app. Sans SubscriptionActivePermission : une clinique
    suspendue doit pouvoir continuer à lire et classer ses alertes."""

    serializer_class = InAppNotificationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.DjangoFilterBackend, SearchFilter]
    filterset_class = InAppNotificationFilter
    search_fields = ["title", "body"]

    def get_queryset(self):
        user = self.request.user
        return InAppNotification.objects.filter(recipient=user, clinic_id=user.clinic_id)

    def _active(self):
        return self.get_queryset().filter(archived_at__isnull=True)

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"count": self._active().filter(read_at__isnull=True).count()})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        updated = self._active().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"updated": updated})

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        notification = self.get_object()
        if notification.archived_at is None:
            now = timezone.now()
            notification.archived_at = now
            notification.read_at = notification.read_at or now
            notification.save(update_fields=["archived_at", "read_at"])
        return Response(self.get_serializer(notification).data)
