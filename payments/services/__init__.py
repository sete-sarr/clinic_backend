from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from billing.services import recompute_invoice_status
from common.currency import format_money

from ..models import Payment


@transaction.atomic
def create_payment(*, clinic, invoice, amount, created_by, **fields):
    if invoice.clinic_id != clinic.id:
        # Message volontairement générique (audit de sécurité, 2026-09-02) : ne confirme pas si
        # l'ID soumis existe dans une autre clinique, afin d'éviter un oracle d'existence cross-tenant.
        raise ValidationError(_("Facture invalide."))
    if amount is None or amount <= Decimal("0.00"):
        raise ValidationError(_("Le montant du paiement doit être positif."))
    if fields.get("date") and fields["date"] < invoice.issue_date:
        raise ValidationError(_("La date du paiement ne peut pas être antérieure à la date de la facture."))
    if amount > invoice.balance_due:
        raise ValidationError(
            _("Le paiement de %(amount)s dépasse le solde restant dû de %(balance)s sur cette facture.")
            % {
                "amount": format_money(amount, invoice.currency),
                "balance": format_money(invoice.balance_due, invoice.currency),
            }
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
    """business/workflow-policy.md : les remboursements nécessitent l'approbation de l'administrateur
    (appliqué au niveau de la couche des permissions)."""
    if payment.status != Payment.Status.VALIDATED:
        raise ValidationError(_("Seul un paiement validé peut être remboursé."))
    payment.status = Payment.Status.REFUNDED
    payment.save(update_fields=["status"])
    recompute_invoice_status(invoice=payment.invoice)
    return payment
