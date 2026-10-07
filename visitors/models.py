"""Registre des visiteurs (docs/visitors.md) : entrées et sorties de toute personne qui pénètre dans
la clinique sans être un patient pris en charge. Minimisation (RGPD) : ni pièce d'identité ni photo ;
conservation 1 an puis suppression automatique (visitors/tasks.py)."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel


class VisitLog(TimeStampedModel):
    class VisitorType(models.TextChoices):
        PATIENT_VISIT = "patient_visit", _("Visite à un patient")
        COMPANION = "companion", _("Accompagnant")
        SUPPLIER = "supplier", _("Fournisseur")
        CONTRACTOR = "contractor", _("Prestataire")
        OTHER = "other", _("Autre")

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.CASCADE, related_name="visit_logs")
    visitor_name = models.CharField(max_length=150)
    visitor_phone = models.CharField(max_length=32, blank=True)
    visitor_type = models.CharField(max_length=20, choices=VisitorType.choices)
    purpose = models.CharField(max_length=255)
    # Personne visitée, facultative : un seul des trois champs est renseigné.
    visited_admission = models.ForeignKey(
        "hospitalization.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="visits"
    )
    visited_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    visited_free_text = models.CharField(max_length=150, blank=True)
    checked_in_at = models.DateTimeField()
    checked_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    checked_out_at = models.DateTimeField(null=True, blank=True)
    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Vrai si la visite, restée ouverte, a été clôturée par la tâche de fin de journée
    # (« sortie non enregistrée », business/workflow-policy.md § WORKFLOW VISITE).
    auto_closed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-checked_in_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "checked_out_at"]),
            models.Index(fields=["clinic", "checked_in_at"]),
        ]

    def __str__(self):
        return f"{self.visitor_name} ({self.checked_in_at:%Y-%m-%d %H:%M})"
