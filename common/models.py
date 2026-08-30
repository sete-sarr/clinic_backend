from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    """Every business table must carry created_at/updated_at (docs/database-design.md)."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Physical deletion is forbidden for business entities (business/validation-rules.md) — archive instead."""

    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class SequenceCounter(models.Model):
    """Reusable per-clinic/per-year sequence generator (patient numbers, invoice numbers...)."""

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.CASCADE, related_name="sequence_counters")
    key = models.CharField(max_length=50)
    year = models.PositiveIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["clinic", "key", "year"], name="unique_sequence_per_clinic_key_year"),
        ]

    @classmethod
    def next_value(cls, *, clinic, key, year):
        """Must be called inside an enclosing transaction.atomic() block."""
        counter, _ = cls.objects.select_for_update().get_or_create(
            clinic=clinic, key=key, year=year, defaults={"last_value": 0}
        )
        counter.last_value += 1
        counter.save(update_fields=["last_value"])
        return counter.last_value


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        CANCEL = "cancel", "Cancel"
        ARCHIVE = "archive", "Archive"
        VIEW = "view", "View"
        PRINT = "print", "Print"
        EXPORT = "export", "Export"
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        PERMISSION_CHANGE = "permission_change", "Permission change"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="audit_logs"
    )
    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.SET_NULL, null=True, related_name="audit_logs"
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clinic", "created_at"]),
            models.Index(fields=["model_name", "object_id"]),
        ]

    def __str__(self):
        return f"{self.action} {self.model_name}#{self.object_id} by user#{self.user_id}"
