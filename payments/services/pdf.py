from django.template.loader import render_to_string
from weasyprint import HTML

from common.audit import record_audit
from common.models import AuditLog


def can_access_payment(*, user, payment):
    """business/reporting-export-policy.md: same ownership rule as the invoice/prescription PDFs."""
    if user.is_superuser:
        return True
    if getattr(user, "clinic_id", None) != payment.clinic_id:
        return False
    patient_profile = getattr(user, "patient_profile", None)
    if patient_profile and payment.invoice.patient_id == patient_profile.id:
        return True
    return user.groups.filter(name__in=["clinic_admin", "accountant", "secretary", "doctor"]).exists()


def render_receipt_pdf(*, payment, user):
    if not can_access_payment(user=user, payment=payment):
        raise PermissionError("You are not allowed to access this payment.")

    html = render_to_string(
        "payments/receipt_pdf.html",
        {"payment": payment, "invoice": payment.invoice, "clinic": payment.clinic},
    )
    pdf_bytes = HTML(string=html).write_pdf()

    record_audit(user=user, action=AuditLog.Action.PRINT, obj=payment, metadata={"document": "receipt_pdf"})
    return pdf_bytes
