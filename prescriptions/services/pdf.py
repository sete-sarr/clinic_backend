from django.template.loader import render_to_string
from weasyprint import HTML

from common.audit import record_audit
from common.models import AuditLog


def can_access_prescription(*, user, prescription):
    """
    docs/security.md / docs/reporting-guidelines.md: prescription_pdf must check
    ownership/role, not just authentication — this is the exact gap those docs
    call out as a priority fix in the legacy codebase this project replaces.
    """
    if user.is_superuser:
        return True
    if getattr(user, "clinic_id", None) != prescription.clinic_id:
        return False
    doctor_profile = getattr(user, "doctor_profile", None)
    if doctor_profile and prescription.doctor_id == doctor_profile.id:
        return True
    patient_profile = getattr(user, "patient_profile", None)
    if patient_profile and prescription.patient_id == patient_profile.id:
        return True
    return user.groups.filter(name="clinic_admin").exists()


def render_prescription_pdf(*, prescription, user):
    if not can_access_prescription(user=user, prescription=prescription):
        raise PermissionError("You are not allowed to access this prescription.")

    html = render_to_string(
        "prescriptions/prescription_pdf.html",
        {"prescription": prescription, "items": prescription.items.all(), "clinic": prescription.clinic},
    )
    pdf_bytes = HTML(string=html).write_pdf()

    record_audit(
        user=user,
        action=AuditLog.Action.PRINT,
        obj=prescription,
        metadata={"document": "prescription_pdf"},
    )
    return pdf_bytes
