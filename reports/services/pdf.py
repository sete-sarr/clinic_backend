from django.template.loader import render_to_string
from weasyprint import HTML

from common.audit import record_audit
from common.models import AuditLog


def render_clinic_activity_report_pdf(*, clinic, user, date_from, date_to):
    """business/reporting-export-policy.md: Clinic Activity Report — clinic_admin only, full clinic
    scope (a doctor's own activity is already visible through the existing appointments/
    consultations screens, so no doctor-scoped variant is built here)."""
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
