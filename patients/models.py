from django.conf import settings
from django.db import models
from django.db.models import Q

from common.models import SoftDeleteModel, TimeStampedModel


class Patient(TimeStampedModel, SoftDeleteModel):
    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"

    class BloodType(models.TextChoices):
        A_POS = "A+", "A+"
        A_NEG = "A-", "A-"
        B_POS = "B+", "B+"
        B_NEG = "B-", "B-"
        AB_POS = "AB+", "AB+"
        AB_NEG = "AB-", "AB-"
        O_POS = "O+", "O+"
        O_NEG = "O-", "O-"
        UNKNOWN = "unknown", "Unknown"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="patient_profile"
    )
    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="patients")
    patient_number = models.CharField(max_length=30, db_index=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=32, db_index=True)
    email = models.EmailField(blank=True)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=10, choices=Gender.choices)
    blood_type = models.CharField(max_length=10, choices=BloodType.choices, default=BloodType.UNKNOWN)
    national_id = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(fields=["clinic", "patient_number"], name="unique_patient_number_per_clinic"),
            models.UniqueConstraint(
                fields=["clinic", "national_id"],
                condition=~Q(national_id=""),
                name="unique_national_id_per_clinic_when_set",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "patient_number"]),
            models.Index(fields=["clinic", "phone"]),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.patient_number})"
