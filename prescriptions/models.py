from django.db import models

from common.models import TimeStampedModel


class Prescription(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        CANCELLED = "cancelled", "Cancelled"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="prescriptions")
    consultation = models.OneToOneField(
        "consultations.Consultation", on_delete=models.PROTECT, related_name="prescription"
    )
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="prescriptions")
    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.PROTECT, related_name="prescriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["clinic"])]

    def __str__(self):
        return f"Prescription for {self.patient} ({self.get_status_display()})"


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="items")
    medication_name = models.CharField(max_length=200)
    dosage = models.CharField(max_length=100)
    frequency = models.CharField(max_length=100)
    duration = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField()
    instructions = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.medication_name} ({self.dosage})"
