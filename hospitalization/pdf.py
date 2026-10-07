from common.audit import record_audit
from common.models import AuditLog
from common.pdf import render_pdf


def render_stay_summary_pdf(*, admission, user):
    """Résumé de séjour (business/reporting-export-policy.md) : médecin, administrateur, infirmier
    en lecture — l'accès est déjà limité par AdmissionViewSet. Jamais la réception ni la comptabilité."""
    pdf_bytes = render_pdf(
        "hospitalization/stay_summary_pdf.html",
        {"admission": admission, "transfers": admission.transfers.all(), "clinic": admission.clinic},
        clinic=admission.clinic,
    )
    record_audit(user=user, action=AuditLog.Action.PRINT, obj=admission, metadata={"document": "stay_summary_pdf"})
    return pdf_bytes
