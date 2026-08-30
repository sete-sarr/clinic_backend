from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from common.models import SequenceCounter

from ..models import DEFAULT_VAT_RATE, Invoice, InvoiceLine

LOCKED_STATUSES = {Invoice.Status.PAID, Invoice.Status.CANCELLED}


def generate_invoice_number(*, clinic, year=None):
    year = year or timezone.now().year
    sequence = SequenceCounter.next_value(clinic=clinic, key="invoice_number", year=year)
    return f"INV-{year}-{sequence:05d}"


def _compute_totals(*, lines, vat_rate):
    subtotal = Decimal("0.00")
    computed_lines = []
    for line in lines:
        quantity = line["quantity"]
        unit_price = line["unit_price"]
        if quantity is None or unit_price is None:
            raise ValidationError("Every invoice line requires a quantity and a unit price.")
        line_total = (Decimal(quantity) * Decimal(unit_price)).quantize(Decimal("0.01"))
        subtotal += line_total
        computed_lines.append({**line, "line_total": line_total})

    vat_amount = (subtotal * vat_rate).quantize(Decimal("0.01"))
    total_amount = subtotal + vat_amount
    return computed_lines, subtotal, vat_amount, total_amount


@transaction.atomic
def create_invoice(*, clinic, patient, lines, doctor=None, vat_rate=DEFAULT_VAT_RATE, issue_date=None, **fields):
    if patient.clinic_id != clinic.id:
        raise ValidationError("Patient does not belong to this clinic.")
    if doctor and doctor.clinic_id != clinic.id:
        raise ValidationError("Doctor does not belong to this clinic.")
    if not lines:
        raise ValidationError("An invoice must contain at least one line.")

    computed_lines, subtotal, vat_amount, total_amount = _compute_totals(lines=lines, vat_rate=vat_rate)

    invoice = Invoice.objects.create(
        clinic=clinic,
        patient=patient,
        doctor=doctor,
        number=generate_invoice_number(clinic=clinic),
        issue_date=issue_date or timezone.now().date(),
        subtotal=subtotal,
        vat_rate=vat_rate,
        vat_amount=vat_amount,
        total_amount=total_amount,
        **fields,
    )
    InvoiceLine.objects.bulk_create([InvoiceLine(invoice=invoice, **line) for line in computed_lines])
    return invoice


@transaction.atomic
def update_invoice(*, invoice, lines=None, **fields):
    if invoice.status in LOCKED_STATUSES:
        raise ValidationError("A paid or cancelled invoice can no longer be edited.")

    if "patient" in fields and fields["patient"].clinic_id != invoice.clinic_id:
        raise ValidationError("Patient does not belong to this clinic.")
    if "doctor" in fields and fields["doctor"] and fields["doctor"].clinic_id != invoice.clinic_id:
        raise ValidationError("Doctor does not belong to this clinic.")

    vat_rate = fields.get("vat_rate", invoice.vat_rate)

    for key, value in fields.items():
        setattr(invoice, key, value)

    if lines is not None:
        if not lines:
            raise ValidationError("An invoice must contain at least one line.")
        computed_lines, subtotal, vat_amount, total_amount = _compute_totals(lines=lines, vat_rate=vat_rate)
        invoice.lines.all().delete()
        InvoiceLine.objects.bulk_create([InvoiceLine(invoice=invoice, **line) for line in computed_lines])
        invoice.subtotal = subtotal
        invoice.vat_amount = vat_amount
        invoice.total_amount = total_amount

    invoice.save()
    return invoice


@transaction.atomic
def issue_invoice(*, invoice):
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError("Only a draft invoice can be issued.")
    invoice.status = Invoice.Status.ISSUED
    invoice.save(update_fields=["status"])
    return invoice


@transaction.atomic
def cancel_invoice(*, invoice):
    if invoice.status == Invoice.Status.PAID:
        raise ValidationError("A paid invoice cannot be cancelled.")
    invoice.status = Invoice.Status.CANCELLED
    invoice.save(update_fields=["status"])
    return invoice


@transaction.atomic
def recompute_invoice_status(*, invoice):
    """Called from payments.services whenever a payment is validated/refunded (business-rules.md)."""
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
        return invoice

    amount_paid = invoice.amount_paid
    if amount_paid >= invoice.total_amount and invoice.total_amount > 0:
        invoice.status = Invoice.Status.PAID
    elif amount_paid > 0:
        invoice.status = Invoice.Status.PENDING_PAYMENT
    else:
        invoice.status = Invoice.Status.ISSUED
    invoice.save(update_fields=["status"])
    return invoice
