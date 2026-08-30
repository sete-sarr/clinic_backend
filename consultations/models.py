from django.db import models

from common.models import TimeStampedModel


class Consultation(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        COMPLETED = "completed", "Completed"
        VALIDATED = "validated", "Validated"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="consultations")
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="consultations")
    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.PROTECT, related_name="consultations")
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultations",
    )
    date = models.DateTimeField()
    is_follow_up = models.BooleanField(default=False)
    chief_complaint = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    treatment_plan = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["clinic", "date"]),
            models.Index(fields=["doctor", "date"]),
            models.Index(fields=["patient", "date"]),
        ]

    def __str__(self):
        return f"Consultation of {self.patient} by {self.doctor} on {self.date:%Y-%m-%d}"
