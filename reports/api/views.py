from datetime import date

from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsClinicAdmin
from reports.services.dashboard import can_see_appointment_stats, can_see_financial_stats, dashboard_stats
from reports.services.pdf import render_clinic_activity_report_pdf


class ClinicActivityReportView(APIView):
    """business/reporting-export-policy.md: Clinic Activity Report is clinic_admin only."""

    permission_classes = [IsAuthenticated, IsClinicAdmin]

    def get(self, request):
        clinic = request.user.clinic
        if clinic is None:
            raise ValidationError(_("Votre compte n'est rattaché à aucune clinique."))

        today = timezone.localdate()
        date_from_raw = request.query_params.get("date_from")
        date_to_raw = request.query_params.get("date_to")
        date_from = date.fromisoformat(date_from_raw) if date_from_raw else today.replace(day=1)
        date_to = date.fromisoformat(date_to_raw) if date_to_raw else today

        try:
            pdf_bytes = render_clinic_activity_report_pdf(
                clinic=clinic, user=request.user, date_from=date_from, date_to=date_to
            )
        except PermissionError:
            return Response({"code": 403, "message": _("Vous n'êtes pas autorisé(e) à accéder à ce rapport."), "field": None}, status=403)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="activity-report-{date_from}-{date_to}.pdf"'
        return response


class DashboardStatsView(APIView):
    """Données des graphiques du tableau de bord, limitées à la clinique de l'utilisateur et aux
    sections de son rôle (reports/services/dashboard.py, business/reporting-export-policy.md)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.clinic is None:
            raise ValidationError(_("Votre compte n'est rattaché à aucune clinique."))
        if not (can_see_appointment_stats(user) or can_see_financial_stats(user)):
            raise PermissionDenied(_("Vous n'êtes pas autorisé(e) à accéder à ce rapport."))
        return Response(dashboard_stats(user=user, clinic=user.clinic, today=timezone.localdate()))
