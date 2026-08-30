from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from weasyprint import HTML

from common.audit import record_audit
from common.models import AuditLog


def can_access_checkin_ticket(*, user, appointment):
    if user.is_superuser:
        return True
    if getattr(user, "clinic_id", None) != appointment.clinic_id:
        return False
    doctor_profile = getattr(user, "doctor_profile", None)
    if doctor_profile and appointment.doctor_id == doctor_profile.id:
        return True
    patient_profile = getattr(user, "patient_profile", None)
    if patient_profile and appointment.patient_id == patient_profile.id:
        return True
    return user.groups.filter(name__in=["secretary", "clinic_admin"]).exists()


def render_checkin_ticket_pdf(*, appointment, user):
    if not can_access_checkin_ticket(user=user, appointment=appointment):
        raise PermissionError("You are not allowed to access this ticket.")
    if appointment.checked_in_at is None:
        raise ValidationError("This appointment has not been checked in yet.")

    html = render_to_string("appointments/checkin_ticket_pdf.html", {"appointment": appointment})
    pdf_bytes = HTML(string=html).write_pdf()

    record_audit(
        user=user,
        action=AuditLog.Action.PRINT,
        obj=appointment,
        metadata={"document": "checkin_ticket_pdf", "ticket_number": appointment.ticket_number},
    )
    return pdf_bytes
