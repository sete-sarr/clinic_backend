import csv

from django.http import HttpResponse
from rest_framework.decorators import action

from .models import AuditLog


class CsvExportMixin:
    """Ajoute une action GET .../export/ qui exporte en CSV le queryset filtré PROPRE au viewset —
    réutilise exactement le même get_queryset()/scoping de permissions que l'endpoint de liste,
    selon business/reporting-export-policy.md ("toute liste qu'un rôle peut déjà consulter via
    l'API, scopée à l'identique"). Ne jamais construire une requête séparée, scopée différemment,
    pour un export."""

    export_fields: list[str] = []

    @action(detail=False, methods=["get"], url_path="export")
    def export_csv(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        fields = self.export_fields or (serializer.data[0].keys() if serializer.data else [])

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{self.basename}-export.csv"'
        writer = csv.DictWriter(response, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in serializer.data:
            writer.writerow(row)

        # Un export en masse n'a pas d'objet unique, donc il est enregistré directement plutôt que
        # via common.audit.record_audit() (qui suppose un seul obj pour en déduire
        # model_name/object_id).
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            clinic=getattr(request.user, "clinic", None),
            action=AuditLog.Action.EXPORT,
            model_name=self.basename,
            object_id="",
            metadata={"row_count": len(serializer.data)},
        )
        return response
