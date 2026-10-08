from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.currency import CURRENCY_CHOICES, DEFAULT_CURRENCY
from common.models import TimeStampedModel
from pharmacy.models import SaleUnit

DEFAULT_VAT_RATE = Decimal("0.18")


class Invoice(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Brouillon")
        ISSUED = "issued", _("Émise")
        PENDING_PAYMENT = "pending_payment", _("Paiement partiel")
        PAID = "paid", _("Payée")
        CANCELLED = "cancelled", _("Annulée")

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
    # Devise de la clinique au moment de la création (common/currency.py) — jamais modifiée ensuite,
    # pour qu'un changement de devise de la clinique ne réinterprète pas les factures existantes.
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=DEFAULT_CURRENCY)

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
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    # Nullable : la plupart des lignes de facture (consultation, acte...) ne correspondent à aucun
    # article de stock. Quand elle est renseignée, pharmacy/services.py::sync_invoice_stock
    # décrémente le stock de ce médicament à l'émission de la facture (business decision,
    # session du 2026-09-16).
    medication = models.ForeignKey(
        "pharmacy.Medication", on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines"
    )
    # Ligne médicament : vendue au conditionnement (boîte) ou à l'unité de base (comprimé).
    # stock_quantity = quantité en unités de base, calculée et figée à l'enregistrement de la ligne
    # (billing/services) — c'est elle que sync_invoice_stock retire du stock. 0 hors médicament.
    sale_unit = models.CharField(max_length=10, choices=SaleUnit.choices, default=SaleUnit.UNIT)
    stock_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.description} x{self.quantity}"
