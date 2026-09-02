from django.template.loader import render_to_string
from weasyprint import HTML

from common.audit import record_audit
from common.models import AuditLog


def can_access_activity_report(*, user, clinic):
    """docs/security.md: PDF rendering must check ownership/role, not just authentication.
    Redundant with reports/api/views.py::ClinicActivityReportView (which already hardcodes
    clinic=request.user.clinic and gates with IsClinicAdmin) — added for defense-in-depth and
    consistency with every other render_*_pdf in the codebase (security audit, 2026-09-02)."""
    if user.is_superuser:
        return True
    if getattr(user, "clinic_id", None) != clinic.id:
        return False
    return user.groups.filter(name="clinic_admin").exists()


def render_clinic_activity_report_pdf(*, clinic, user, date_from, date_to):
    """business/reporting-export-policy.md: Clinic Activity Report — clinic_admin only, full clinic
    scope (a doctor's own activity is already visible through the existing appointments/
    consultations screens, so no doctor-scoped variant is built here)."""
    if not can_access_activity_report(user=user, clinic=clinic):
        raise PermissionError("You are not allowed to access this report.")

    from appointments.models import Appointment
    from consultations.models import Consultation

    appointments = (
        Appointment.objects.filter(clinic=clinic, date__gte=date_from, date__lte=date_to)
        .select_related("patient", "doctor__user")
        .order_by("date", "time")
    )
    consultations = (
        Consultation.objects.filter(clinic=clinic, date__date__gte=date_from, date__date__lte=date_to)
        .select_related("patient", "doctor__user")
        .order_by("date")
    )

    html = render_to_string(
        "reports/activity_report_pdf.html",
        {
            "clinic": clinic,
            "date_from": date_from,
            "date_to": date_to,
            "appointments": appointments,
            "consultations": consultations,
            "appointments_count": appointments.count(),
            "consultations_count": consultations.count(),
        },
    )
    pdf_bytes = HTML(string=html).write_pdf()

    record_audit(
        user=user,
        action=AuditLog.Action.PRINT,
        obj=clinic,
        metadata={"document": "clinic_activity_report_pdf", "date_from": str(date_from), "date_to": str(date_to)},
    )
    return pdf_bytes
