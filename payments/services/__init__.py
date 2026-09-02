from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from billing.services import recompute_invoice_status

from ..models import Payment


@transaction.atomic
def create_payment(*, clinic, invoice, amount, created_by, **fields):
    if invoice.clinic_id != clinic.id:
        # Deliberately generic (security audit, 2026-09-02): does not confirm whether the
        # submitted ID exists in another clinic, to avoid a cross-tenant existence oracle.
        raise ValidationError("Invalid invoice.")
    if amount is None or amount <= Decimal("0.00"):
        raise ValidationError("Payment amount must be positive.")
    if fields.get("date") and fields["date"] < invoice.issue_date:
        raise ValidationError("Payment date cannot be before the invoice date.")
    if amount > invoice.balance_due:
        raise ValidationError(
            f"Payment of {amount} exceeds the outstanding balance of {invoice.balance_due} on this invoice."
        )

    payment = Payment.objects.create(
        clinic=clinic,
        invoice=invoice,
        amount=amount,
        created_by=created_by,
        status=Payment.Status.VALIDATED,
        **fields,
    )
    recompute_invoice_status(invoice=invoice)
    return payment


@transaction.atomic
def refund_payment(*, payment):
    """business/workflow-policy.md: refunds require Administrator approval (enforced in the permission layer)."""
    if payment.status != Payment.Status.VALIDATED:
        raise ValidationError("Only a validated payment can be refunded.")
    payment.status = Payment.Status.REFUNDED
    payment.save(update_fields=["status"])
    recompute_invoice_status(invoice=payment.invoice)
    return payment
