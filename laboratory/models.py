"""Laboratoire (docs/laboratory.md) : catalogue d'examens, demandes, résultats.

Le statut d'un résultat (business/workflow-policy.md § RÉSULTAT DE LABORATOIRE) n'est pas stocké :
« En attente » = aucun résultat courant, « Disponible » = résultat saisi, « Validé » = demande validée."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import SoftDeleteModel, TimeStampedModel


class LabTest(TimeStampedModel, SoftDeleteModel):
    """Examen du catalogue de la clinique. Prix et valeurs de référence saisis par la clinique."""

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="lab_tests")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=200)
    price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )
    unit = models.CharField(max_length=30, blank=True, help_text="Ex. g/L, mmol/L.")
    reference_min = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    reference_max = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["clinic", "code"], name="unique_lab_test_code_per_clinic"),
            models.CheckConstraint(condition=models.Q(price__gte=0), name="lab_test_price_non_negative"),
            models.CheckConstraint(
                condition=(
                    models.Q(reference_min__isnull=True)
                    | models.Q(reference_max__isnull=True)
                    | models.Q(reference_max__gte=models.F("reference_min"))
                ),
                name="lab_test_reference_max_gte_min",
            ),
        ]
        indexes = [models.Index(fields=["clinic", "is_active"])]

    def __str__(self):
        return f"{self.code} — {self.name}"


class LabOrder(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", _("Demandé")
        COLLECTED = "collected", _("Prélevé")
        IN_PROGRESS = "in_progress", _("En traitement")
        COMPLETED = "completed", _("Terminé")
        VALIDATED = "validated", _("Validé")
        CANCELLED = "cancelled", _("Annulé")

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="lab_orders")
    number = models.CharField(max_length=30)
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="lab_orders")
    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.PROTECT, related_name="lab_orders")
    consultation = models.ForeignKey(
        "consultations.Consultation", on_delete=models.PROTECT, null=True, blank=True, related_name="lab_orders"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    # Renseignement clinique utile au laboratoire — jamais le diagnostic détaillé (access-policy.md).
    clinical_note = models.TextField(blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Facture brouillon créée à la validation (docs/laboratory.md §6) — jamais exposée au technicien.
    invoice = models.ForeignKey("billing.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="lab_orders")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["clinic", "number"], name="unique_lab_order_number_per_clinic")]
        indexes = [
            models.Index(fields=["clinic", "status"]),
            models.Index(fields=["patient"]),
            models.Index(fields=["doctor"]),
        ]

    def __str__(self):
        return self.number


class LabOrderItem(models.Model):
    """Un examen demandé. Nom, unité, valeurs de référence et prix sont figés à la demande : la
    facture et le PDF reprennent ces valeurs, jamais celles, modifiables, du catalogue."""

    order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="items")
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="order_items")
    test_code = models.CharField(max_length=30)
    test_name = models.CharField(max_length=200)
    unit = models.CharField(max_length=30, blank=True)
    reference_min = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    reference_max = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.order.number} — {self.test_name}"

    @property
    def current_result(self):
        """Résultat en vigueur (le plus récent non remplacé). S'appuie sur le prefetch `results`."""
        return next((result for result in self.results.all() if result.superseded_at is None), None)


class LabResult(models.Model):
    """Résultat d'un examen. Un résultat validé n'est jamais modifié : une correction crée un
    nouveau résultat motivé (`correction_reason`) et marque l'ancien comme remplacé."""

    item = models.ForeignKey(LabOrderItem, on_delete=models.CASCADE, related_name="results")
    value = models.CharField(max_length=100, blank=True)
    # Calculé par le serveur (laboratory/services.py::is_abnormal), jamais saisi.
    is_abnormal = models.BooleanField(default=False)
    comment = models.TextField(blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    entered_at = models.DateTimeField(auto_now_add=True)
    correction_reason = models.TextField(blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-entered_at", "-id"]
        indexes = [models.Index(fields=["item", "superseded_at"])]

    def __str__(self):
        return f"{self.item} : {self.value}"


class LabResultFile(models.Model):
    """PDF joint à un résultat, stocké en base (même raison que clinics.ClinicLogo : le disque de
    l'hébergeur n'est pas persistant). Modèle séparé pour ne jamais charger ces octets en liste."""

    result = models.OneToOneField(LabResult, on_delete=models.CASCADE, related_name="attachment")
    filename = models.CharField(max_length=200)
    content = models.BinaryField()
    size = models.PositiveIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.filename
