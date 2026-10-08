from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from common.models import SoftDeleteModel, TimeStampedModel


class SaleUnit(models.TextChoices):
    """Unité d'une quantité saisie (réception, ligne de facture) : le conditionnement (boîte…) ou
    l'unité de base (comprimé…). Le stock, lui, est toujours compté en unités de base."""

    PACK = "pack", "Conditionnement"
    UNIT = "unit", "Unité"


class Medication(TimeStampedModel, SoftDeleteModel):
    """Catalogue de médicaments par clinique (isolation tenant, docs/architecture.md).

    Unités (décision produit du 2026-10-08) : tout le stock est compté en entiers dans l'unité de
    base `unit`, la plus petite unité délivrée (comprimé, gélule, flacon…). Le conditionnement
    (`pack_unit`, ex. boîte) n'est qu'un facteur de conversion, `units_per_pack`, utilisé à la
    réception, à la vente et à l'affichage ; il vaut 1 pour un produit qui ne se détaille pas.

    `current_stock` est une valeur dénormalisée = somme de StockBatch.quantity_remaining pour ce
    médicament, recalculée à chaque mouvement (pharmacy/services.py) — jamais modifiée directement,
    pour rester la source rapide de lecture utilisée par la vérification de seuil sans agréger les
    lots à chaque requête."""

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="medications")
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=50, help_text="Unité de base. Ex. comprimé, gélule, flacon.")
    # Prix de l'unité de base (vente au détail), saisi librement : il n'est pas forcément égal au
    # prix du conditionnement divisé par units_per_pack.
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )
    pack_unit = models.CharField(max_length=50, blank=True, help_text="Ex. boîte, plaquette.")
    units_per_pack = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    pack_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )
    # Vente à l'unité de base d'un produit conditionné (ex. 3 comprimés d'une boîte de 50).
    allow_unit_sale = models.BooleanField(default=True)
    # En unités de base, comme les seuils ci-dessous.
    current_stock = models.PositiveIntegerField(default=0)
    # 0 = pas d'alerte de stock bas configurée pour ce médicament (valeur par défaut). max_threshold
    # est nullable pour la même raison ("pas de plafond configuré") sans avoir besoin d'une valeur
    # sentinelle ambiguë comme 0, qui serait un plafond légitime si un jour un médicament ne devait
    # jamais être en stock.
    min_threshold = models.PositiveIntegerField(default=0)
    max_threshold = models.PositiveIntegerField(null=True, blank=True)
    # Débounce des alertes de seuil (pharmacy/services.py::check_stock_thresholds) — évite de
    # renvoyer une notification à chaque mouvement tant que le stock reste hors limites ; remis à
    # False dès que le stock revient dans la fourchette [min_threshold, max_threshold].
    low_stock_alerted = models.BooleanField(default=False)
    overstock_alerted = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["clinic", "name"], name="unique_medication_name_per_clinic"),
            models.CheckConstraint(
                condition=(
                    models.Q(max_threshold__isnull=True) | models.Q(max_threshold__gte=models.F("min_threshold"))
                ),
                name="medication_max_threshold_gte_min_threshold",
            ),
            models.CheckConstraint(condition=models.Q(units_per_pack__gte=1), name="medication_units_per_pack_gte_1"),
        ]
        indexes = [models.Index(fields=["clinic"])]

    def __str__(self):
        return self.name

    @property
    def is_packaged(self):
        return self.units_per_pack > 1

    def units_for(self, quantity, sale_unit):
        """Quantité saisie dans `sale_unit` -> unités de base."""
        return quantity * self.units_per_pack if sale_unit == SaleUnit.PACK else quantity


class StockBatch(TimeStampedModel):
    """Un lot reçu à l'achat (business decision, session du 2026-09-16 : suivi de lot/péremption
    dès la v1). Quantités en unités de base ; `received_in` et `units_per_pack` gardent la saisie
    d'origine (ex. 10 boîtes de 50), `unit_cost` étant le coût de cette unité saisie (information
    d'achat, sans effet sur le stock). `quantity_remaining` diminue au fil des dispensations (FEFO, voir
    pharmacy/services.py::_dispense) et remonte en cas d'annulation/modification de facture."""

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="stock_batches")
    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name="batches")
    batch_number = models.CharField(max_length=100)
    expiry_date = models.DateField()
    received_date = models.DateField()
    quantity_received = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    quantity_remaining = models.PositiveIntegerField()
    received_in = models.CharField(max_length=10, choices=SaleUnit.choices, default=SaleUnit.UNIT)
    units_per_pack = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )
    supplier = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["expiry_date", "received_date"]
        indexes = [
            models.Index(fields=["clinic", "medication"]),
            models.Index(fields=["medication", "expiry_date"]),
        ]

    def __str__(self):
        return f"{self.medication.name} lot {self.batch_number} (péremption {self.expiry_date})"


class StockMovement(TimeStampedModel):
    """Journal immuable de chaque mouvement de stock — sert de piste d'audit métier
    (business/access-policy.md : opérations sensibles) et de source de vérité pour la
    réconciliation des lignes de facture (pharmacy/services.py::sync_invoice_stock)."""

    class MovementType(models.TextChoices):
        PURCHASE = "purchase", "Achat"
        DISPENSE = "dispense", "Dispensation (facture)"
        RETURN = "return", "Retour au stock (modification/annulation de facture)"
        ADJUSTMENT = "adjustment", "Ajustement manuel"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="stock_movements")
    medication = models.ForeignKey(Medication, on_delete=models.PROTECT, related_name="movements")
    # Toujours renseigné, y compris pour un ADJUSTMENT : current_stock reste exactement la somme
    # de StockBatch.quantity_remaining pour ce médicament (source de vérité unique, pas de double
    # comptabilité) — un ajustement manuel corrige donc explicitement un lot précis (ex. casse
    # constatée lors d'un inventaire physique), jamais le total de façon détachée d'un lot.
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    # Signé : positif = entrée de stock (achat, retour, ajustement +), négatif = sortie (dispense,
    # ajustement -). Permet d'agréger avec une simple Sum() plutôt que de porter une direction
    # séparée du type de mouvement.
    quantity_delta = models.IntegerField()
    invoice = models.ForeignKey(
        "billing.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements"
    )
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clinic", "medication"]),
            models.Index(fields=["invoice"]),
        ]

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.quantity_delta:+d} — {self.medication.name}"
