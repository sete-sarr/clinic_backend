from datetime import date

from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.permissions import IsClinicAdmin
from reports.services.pdf import render_clinic_activity_report_pdf


class ClinicActivityReportView(APIView):
    """business/reporting-export-policy.md: Clinic Activity Report is clinic_admin only."""

    permission_classes = [IsAuthenticated, IsClinicAdmin]

    def get(self, request):
        clinic = request.user.clinic
        if clinic is None:
            raise ValidationError("Your account is not attached to a clinic.")

        today = timezone.localdate()
        date_from_raw = request.query_params.get("date_from")
        date_to_raw = request.query_params.get("date_to")
        date_from = date.fromisoformat(date_from_raw) if date_from_raw else today.replace(day=1)
        date_to = date.fromisoformat(date_to_raw) if date_to_raw else today

        pdf_bytes = render_clinic_activity_report_pdf(
            clinic=clinic, user=request.user, date_from=date_from, date_to=date_to
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="activity-report-{date_from}-{date_to}.pdf"'
        return response
