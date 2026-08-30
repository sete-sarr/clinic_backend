from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    clinic is nullable only for platform-level superusers; every doctor,
    secretary, accountant, clinic_admin, or patient user must have one
    (docs/architecture.md: "an authenticated user is attached to one clinic").
    """

    clinic = models.ForeignKey(
        "clinics.Clinic", on_delete=models.PROTECT, null=True, blank=True, related_name="users"
    )

    def __str__(self):
        return self.get_username()
