from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from common.audit import record_audit
from common.models import AuditLog

from .models import Department


@transaction.atomic
def archive_department(*, department, actor):
    """business/validation-rules.md: departments with active doctors can never be deleted/archived
    — appointments and consultations are guarded transitively, since both require a doctor, who in
    turn requires a department (business/workflow-policy.md: "Les départements ne sont jamais
    supprimés physiquement", "Seuls les Administrateurs de Clinique peuvent archiver ... des
    départements" — role is enforced by the view's permission_classes, not here)."""
    active_doctor_count = department.doctors.filter(is_active=True).count()
    if active_doctor_count:
        raise ValidationError(
            "This department has active doctors assigned and cannot be archived. "
            "Reassign or deactivate them first."
        )
    department.status = Department.Status.ARCHIVED
    department.is_active = False
    department.archived_at = timezone.now()
    department.save(update_fields=["status", "is_active", "archived_at"])
    record_audit(user=actor, action=AuditLog.Action.ARCHIVE, obj=department)
    return department


@transaction.atomic
def restore_department(*, department, actor):
    department.status = Department.Status.ACTIVE
    department.is_active = True
    department.archived_at = None
    department.save(update_fields=["status", "is_active", "archived_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=department, metadata={"reason": "restored"})
    return department


def deactivate_department(*, department, actor):
    """Actif -> Inactif (business/workflow-policy.md). Distinct from archive(): the department stays
    editable and its doctors stay assigned, it just stops accepting new appointments
    (validation-rules.md: "Les départements inactifs ne peuvent pas recevoir de nouveaux
    rendez-vous" — enforced wherever appointments validate their doctor's department, not here)."""
    if department.status == Department.Status.ARCHIVED:
        raise ValidationError("Archived departments are read-only. Restore it first.")
    department.status = Department.Status.INACTIVE
    department.save(update_fields=["status"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=department, metadata={"reason": "deactivated"})
    return department


def activate_department(*, department, actor):
    if department.status == Department.Status.ARCHIVED:
        raise ValidationError("Archived departments are read-only. Restore it first.")
    department.status = Department.Status.ACTIVE
    department.save(update_fields=["status"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=department, metadata={"reason": "activated"})
    return department
