from django.template.loader import render_to_string
from weasyprint import HTML

from common.audit import record_audit
from common.models import AuditLog


def can_access_statement(*, user, patient):
    """business/reporting-export-policy.md: patient statement is a financial report — clinic_admin,
    accountant, and secretary may access it; doctor may not ("Doctor ... Never financial reports")."""
    if user.is_superuser:
        return True
    if getattr(user, "clinic_id", None) != patient.clinic_id:
        return False
    patient_profile = getattr(user, "patient_profile", None)
    if patient_profile and patient.id == patient_profile.id:
        return True
    return user.groups.filter(name__in=["clinic_admin", "accountant", "secretary"]).exists()


def render_patient_statement_pdf(*, patient, user, date_from=None, date_to=None):
    if not can_access_statement(user=user, patient=patient):
        raise PermissionError("You are not allowed to access this patient's statement.")

    invoices = patient.invoices.select_related("clinic").prefetch_related("lines").order_by("-issue_date")
    if date_from:
        invoices = invoices.filter(issue_date__gte=date_from)
    if date_to:
        invoices = invoices.filter(issue_date__lte=date_to)

    from payments.models import Payment

    payments = Payment.objects.filter(invoice__patient=patient).select_related("invoice").order_by("-date")
    if date_from:
        payments = payments.filter(date__gte=date_from)
    if date_to:
        payments = payments.filter(date__lte=date_to)

    html = render_to_string(
        "patients/statement_pdf.html",
        {
            "patient": patient,
            "clinic": patient.clinic,
            "invoices": invoices,
            "payments": payments,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    pdf_bytes = HTML(string=html).write_pdf()

    record_audit(
        user=user,
        action=AuditLog.Action.PRINT,
        obj=patient,
        metadata={"document": "patient_statement_pdf", "date_from": str(date_from), "date_to": str(date_to)},
    )
    return pdf_bytes
