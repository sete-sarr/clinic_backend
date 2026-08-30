from django.db import models

from common.models import TimeStampedModel


class MedicalRecord(TimeStampedModel):
    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="medical_records")
    patient = models.OneToOneField("patients.Patient", on_delete=models.PROTECT, related_name="medical_record")
    allergies = models.TextField(blank=True)
    medical_history = models.TextField(blank=True)
    observations = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["clinic"])]

    def __str__(self):
        return f"Medical record of {self.patient}"
