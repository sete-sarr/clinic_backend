from common.audit import record_audit
from common.models import AuditLog
from common.pdf import render_pdf


def render_lab_results_pdf(*, order, user):
    """Compte rendu des résultats validés (business/reporting-export-policy.md § Résultats
    d'Examens). L'accès est déjà limité par LabOrderViewSet (rôles et périmètre) : médecin
    prescripteur, administrateur, patient concerné — jamais le technicien."""
    items = [(item, item.current_result) for item in order.items.all()]
    pdf_bytes = render_pdf(
        "laboratory/lab_results_pdf.html",
        {"order": order, "items": items, "clinic": order.clinic},
        clinic=order.clinic,
    )
    record_audit(user=user, action=AuditLog.Action.PRINT, obj=order, metadata={"document": "lab_results_pdf"})
    return pdf_bytes
