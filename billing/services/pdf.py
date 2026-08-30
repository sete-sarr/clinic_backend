from django.template.loader import render_to_string
from weasyprint import HTML

from common.audit import record_audit
from common.models import AuditLog


def can_access_invoice(*, user, invoice):
    """docs/security.md: facture_pdf must check ownership/role, not just authentication."""
    if user.is_superuser:
        return True
    if getattr(user, "clinic_id", None) != invoice.clinic_id:
        return False
    patient_profile = getattr(user, "patient_profile", None)
    if patient_profile and invoice.patient_id == patient_profile.id:
        return True
    return user.groups.filter(name__in=["clinic_admin", "accountant"]).exists()


def render_invoice_pdf(*, invoice, user):
    if not can_access_invoice(user=user, invoice=invoice):
        raise PermissionError("You are not allowed to access this invoice.")

    html = render_to_string(
        "billing/invoice_pdf.html",
        {"invoice": invoice, "lines": invoice.lines.all(), "clinic": invoice.clinic},
    )
    pdf_bytes = HTML(string=html).write_pdf()

    record_audit(user=user, action=AuditLog.Action.PRINT, obj=invoice, metadata={"document": "invoice_pdf"})
    return pdf_bytes
