from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from common.models import TimeStampedModel

DEFAULT_VAT_RATE = Decimal("0.18")


class Invoice(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="invoices")
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="invoices")
    doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices"
    )
    number = models.CharField(max_length=30, db_index=True)
    issue_date = models.DateField()
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    vat_rate = models.DecimalField(max_digits=5, decimal_places=4, default=DEFAULT_VAT_RATE)
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-issue_date", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["clinic", "number"], name="unique_invoice_number_per_clinic"),
        ]
        indexes = [
            models.Index(fields=["clinic", "number"]),
            models.Index(fields=["clinic", "status"]),
            models.Index(fields=["patient"]),
        ]

    @property
    def amount_paid(self):
        return self.payments.filter(status="validated").aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0.00")

    @property
    def balance_due(self):
        return self.total_amount - self.amount_paid

    def __str__(self):
        return self.number or f"Invoice draft #{self.pk}"


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    # Nullable : la plupart des lignes de facture (consultation, acte...) ne correspondent à aucun
    # article de stock. Quand elle est renseignée, pharmacy/services.py::sync_invoice_stock
    # décrémente le stock de ce médicament à l'émission de la facture (business decision,
    # session du 2026-09-16).
    medication = models.ForeignKey(
        "pharmacy.Medication", on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines"
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.description} x{self.quantity}"
