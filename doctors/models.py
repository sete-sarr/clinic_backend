from django.conf import settings
from django.db import models

from common.models import SoftDeleteModel, TimeStampedModel


class Doctor(TimeStampedModel, SoftDeleteModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="doctor_profile")
    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="doctors")
    department = models.ForeignKey(
        "departments.Department", on_delete=models.PROTECT, related_name="doctors", null=True, blank=True
    )
    professional_number = models.CharField(max_length=50)
    specialty = models.CharField(max_length=150)
    phone = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["user__last_name", "user__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "professional_number"], name="unique_professional_number_per_clinic"
            ),
        ]
        indexes = [models.Index(fields=["clinic"])]

    def __str__(self):
        return self.user.get_full_name() or self.user.get_username()
