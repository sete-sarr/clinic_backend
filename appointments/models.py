from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel


class Appointment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("En attente")
        CONFIRMED = "confirmed", _("Confirmé")
        COMPLETED = "completed", _("Terminé")
        CANCELLED = "cancelled", _("Annulé")
        NO_SHOW = "no_show", _("Absence")

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="appointments")
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="appointments")
    # Rendez-vous de département (rotation des médecins) : `doctor` reste vide tant qu'aucun médecin
    # du département ne l'a pris en charge ou ne s'est vu attribuer le rendez-vous
    # (appointments/services.py::take_appointment / assign_appointment). Un rendez-vous avec médecin
    # porte aussi le département de ce médecin.
    doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.PROTECT, related_name="appointments", null=True, blank=True
    )
    department = models.ForeignKey(
        "departments.Department", on_delete=models.PROTECT, related_name="appointments", null=True, blank=True
    )
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
            models.Index(fields=["department", "date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(doctor__isnull=False) | models.Q(department__isnull=False),
                name="appointment_has_doctor_or_department",
            ),
        ]

    def __str__(self):
        return f"{self.patient} with {self.doctor or self.department} on {self.date} {self.time}"
