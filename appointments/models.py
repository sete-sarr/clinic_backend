from django.db import models

from common.models import TimeStampedModel


class Appointment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No-show"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="appointments")
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="appointments")
    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.PROTECT, related_name="appointments")
    date = models.DateField()
    time = models.TimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reason = models.CharField(max_length=255, blank=True)
    day_before_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    # Enregistrement à l'accueil — volontairement un timestamp, pas un nouveau membre de Status
    # (les états aspirationnels "Checked In"/"Waiting" de business/workflow-policy.md n'ont jamais
    # été implémentés comme une véritable machine à états, voir la note de réconciliation de
    # docs/known-issues.md ; non retravaillé ici).
    checked_in_at = models.DateTimeField(null=True, blank=True)
    ticket_number = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["date", "time"]
        indexes = [
            models.Index(fields=["clinic", "date"]),
            models.Index(fields=["doctor", "date"]),
            models.Index(fields=["patient", "date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["clinic", "checked_in_at"]),
        ]

    def __str__(self):
        return f"{self.patient} with {self.doctor} on {self.date} {self.time}"
