import csv

from django.http import HttpResponse
from rest_framework.decorators import action

from .models import AuditLog


class CsvExportMixin:
    """Adds a GET .../export/ action that dumps the viewset's OWN filtered queryset to CSV —
    reuses the exact same get_queryset()/permission scoping as the list endpoint, per
    business/reporting-export-policy.md ("any list a role can already view via the API, scoped
    identically"). Never build a separate, differently-scoped query for an export."""

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

        # A bulk export has no single object, so it's recorded directly rather than through
        # common.audit.record_audit() (which assumes one obj to derive model_name/object_id from).
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            clinic=getattr(request.user, "clinic", None),
            action=AuditLog.Action.EXPORT,
            model_name=self.basename,
            object_id="",
            metadata={"row_count": len(serializer.data)},
        )
        return response
