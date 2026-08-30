from django.db import models

from common.models import SoftDeleteModel, TimeStampedModel


class Department(TimeStampedModel, SoftDeleteModel):
    # business/validation-rules.md VALIDATION DÉPARTEMENT: Actif -> Inactif -> Archivé. `is_active`
    # (from SoftDeleteModel) stays the single "is this Archivé" switch queries/permissions key off
    # of (mirrors every other soft-deletable model); `status` carries the extra Actif/Inactif
    # distinction that only applies while is_active=True.
    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        INACTIVE = "inactive", "Inactif"
        ARCHIVED = "archived", "Archivé"

    class DepartmentType(models.TextChoices):
        MEDICAL = "medical", "Médical"
        ADMINISTRATIVE = "administrative", "Administratif"
        TECHNICAL = "technical", "Technique"
        SUPPORT = "support", "Support"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="departments")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30)
    department_type = models.CharField(max_length=20, choices=DepartmentType.choices)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["clinic", "code"], name="unique_department_code_per_clinic"),
            models.UniqueConstraint(fields=["clinic", "name"], name="unique_department_name_per_clinic"),
        ]
        indexes = [models.Index(fields=["clinic"])]

    def __str__(self):
        return self.name
