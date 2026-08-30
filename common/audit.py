from .models import AuditLog


def record_audit(*, user, action, obj, metadata=None):
    """Log a sensitive operation. Call from services, never from views (docs/backend-guidelines.md)."""
    clinic = getattr(obj, "clinic", None) or getattr(user, "clinic", None)
    return AuditLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        clinic=clinic,
        action=action,
        model_name=obj.__class__.__name__,
        object_id=str(getattr(obj, "pk", "") or ""),
        metadata=metadata or {},
    )
