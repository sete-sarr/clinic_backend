from django.conf import settings
from django.db import models

from common.models import TimeStampedModel


class Payment(TimeStampedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        MOBILE_MONEY = "mobile_money", "Mobile money"
        CARD = "card", "Card"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VALIDATED = "validated", "Validated"
        REFUNDED = "refunded", "Refunded"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.PROTECT, related_name="payments")
    invoice = models.ForeignKey("billing.Invoice", on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="payments_created"
    )

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["clinic"]),
            models.Index(fields=["invoice"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Payment {self.amount} on {self.invoice}"
