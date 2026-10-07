"""Hospitalisation (docs/hospitalization.md) : types de chambre, chambres, lits, séjours, historique
des lits, constantes et notes de soins. Les services de la clinique réutilisent
departments.Department (aucun modèle parallèle). Les statuts de lit et de séjour ne changent que par
hospitalization/services.py (business/workflow-policy.md § WORKFLOW SÉJOUR, WORKFLOW LIT)."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import SoftDeleteModel, TimeStampedModel


class RoomType(TimeStampedModel, SoftDeleteModel):
    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="room_types")
    name = models.CharField(max_length=100)
    # Tarif utilisé en mode « tarif par type de chambre » (Clinic.inpatient_billing_mode).
    nightly_rate = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["clinic", "name"], name="unique_room_type_name_per_clinic"),
            models.CheckConstraint(condition=models.Q(nightly_rate__gt=0), name="room_type_rate_positive"),
        ]

    def __str__(self):
        return self.name


class Room(TimeStampedModel, SoftDeleteModel):
    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="rooms")
    department = models.ForeignKey("departments.Department", on_delete=models.PROTECT, related_name="rooms")
    number = models.CharField(max_length=30)
    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, related_name="rooms")

    class Meta:
        ordering = ["department__name", "number"]
        constraints = [models.UniqueConstraint(fields=["clinic", "number"], name="unique_room_number_per_clinic")]

    def __str__(self):
        return self.number


class Bed(TimeStampedModel, SoftDeleteModel):
    class Status(models.TextChoices):
        FREE = "free", _("Libre")
        OCCUPIED = "occupied", _("Occupé")
        CLEANING = "cleaning", _("En nettoyage")
        OUT_OF_SERVICE = "out_of_service", _("Hors service")

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="beds")
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="beds")
    label = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.FREE)

    class Meta:
        ordering = ["room__number", "label"]
        constraints = [models.UniqueConstraint(fields=["room", "label"], name="unique_bed_label_per_room")]
        indexes = [models.Index(fields=["clinic", "status"])]

    def __str__(self):
        return f"{self.room.number}-{self.label}"


class Admission(TimeStampedModel):
    class Status(models.TextChoices):
        PLANNED = "planned", _("Planifié")
        ADMITTED = "admitted", _("Admis")
        DISCHARGED = "discharged", _("Sorti")
        CANCELLED = "cancelled", _("Annulé")

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="admissions")
    number = models.CharField(max_length=30)
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="admissions")
    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.PROTECT, related_name="admissions")
    department = models.ForeignKey("departments.Department", on_delete=models.PROTECT, related_name="admissions")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, null=True, blank=True, related_name="admissions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    # Motif médical : jamais exposé à la réception ni à la comptabilité (access-policy.md).
    reason = models.TextField()
    planned_for = models.DateField(null=True, blank=True)
    admitted_at = models.DateTimeField(null=True, blank=True)
    admitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    discharged_at = models.DateTimeField(null=True, blank=True)
    discharged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    discharge_summary = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Calculé à la sortie (une nuitée par passage de minuit) et facturé dans `invoice`.
    nights = models.PositiveIntegerField(null=True, blank=True)
    invoice = models.ForeignKey("billing.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="admissions")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["clinic", "number"], name="unique_admission_number_per_clinic"),
            # Un lit ne porte qu'un séjour Admis ; un patient n'a qu'un séjour Admis (workflow-policy.md).
            models.UniqueConstraint(
                fields=["bed"], condition=models.Q(status="admitted"), name="one_admitted_stay_per_bed"
            ),
            models.UniqueConstraint(
                fields=["patient"], condition=models.Q(status="admitted"), name="one_admitted_stay_per_patient"
            ),
        ]
        indexes = [models.Index(fields=["clinic", "status"])]

    def __str__(self):
        return self.number


class BedTransfer(models.Model):
    """Historique complet des lits d'un séjour : l'admission est la première ligne (from_bed vide),
    chaque transfert en ajoute une. Sert au décompte des nuitées par type de chambre."""

    admission = models.ForeignKey(Admission, on_delete=models.PROTECT, related_name="transfers")
    from_bed = models.ForeignKey(Bed, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    to_bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="+")
    transferred_at = models.DateTimeField()
    transferred_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["transferred_at", "id"]


class VitalSign(models.Model):
    admission = models.ForeignKey(Admission, on_delete=models.PROTECT, related_name="vital_signs")
    recorded_at = models.DateTimeField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse = models.PositiveSmallIntegerField(null=True, blank=True)
    respiratory_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    oxygen_saturation = models.PositiveSmallIntegerField(null=True, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    pain = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]


class NursingNote(models.Model):
    """Jamais modifiée : une correction est une nouvelle note (permissions-matrix.md)."""

    admission = models.ForeignKey(Admission, on_delete=models.PROTECT, related_name="nursing_notes")
    note = models.TextField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-recorded_at", "-id"]
