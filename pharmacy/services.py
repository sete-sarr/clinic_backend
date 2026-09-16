from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum

from common.audit import record_audit
from common.models import AuditLog

from .models import Medication, StockBatch, StockMovement

# Statuts de facture pour lesquels les lignes médicament sont considérées "dispensées" (stock
# consommé). Décision produit (session du 2026-09-16) : la décrémentation a lieu à l'émission —
# Draft n'en fait donc jamais partie. Voir sync_invoice_stock().
DISPENSING_STATUSES = {"issued", "pending_payment", "paid"}


@transaction.atomic
def create_medication(*, clinic, actor, **fields):
    medication = Medication.objects.create(clinic=clinic, **fields)
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=medication)
    return medication


@transaction.atomic
def update_medication(*, medication, actor, **fields):
    for key, value in fields.items():
        setattr(medication, key, value)
    medication.save()
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=medication)
    # Les seuils ont pu changer sans qu'aucun mouvement de stock n'ait eu lieu — revérifier ici
    # évite de rater une alerte tant qu'un mouvement ultérieur ne la déclenche pas.
    check_stock_thresholds(medication)
    return medication


@transaction.atomic
def archive_medication(*, medication, actor):
    from django.utils import timezone

    medication.is_active = False
    medication.archived_at = timezone.now()
    medication.save(update_fields=["is_active", "archived_at"])
    record_audit(user=actor, action=AuditLog.Action.ARCHIVE, obj=medication)
    return medication


@transaction.atomic
def restore_medication(*, medication, actor):
    medication.is_active = True
    medication.archived_at = None
    medication.save(update_fields=["is_active", "archived_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=medication, metadata={"reason": "restored"})
    return medication


def _recompute_current_stock(medication):
    total = medication.batches.aggregate(total=Sum("quantity_remaining"))["total"] or 0
    medication.current_stock = total
    medication.save(update_fields=["current_stock"])
    check_stock_thresholds(medication)


def check_stock_thresholds(medication):
    """Débounce : n'alerte qu'au franchissement du seuil (pas à chaque mouvement tant que le
    stock reste hors limites), et réarme dès que le stock revient dans la fourchette."""
    changed_fields = []

    is_low = bool(medication.min_threshold) and medication.current_stock < medication.min_threshold
    if is_low and not medication.low_stock_alerted:
        medication.low_stock_alerted = True
        changed_fields.append("low_stock_alerted")
        from .notifications import send_low_stock_alert

        transaction.on_commit(lambda: send_low_stock_alert(medication_id=medication.id))
    elif not is_low and medication.low_stock_alerted:
        medication.low_stock_alerted = False
        changed_fields.append("low_stock_alerted")

    is_over = medication.max_threshold is not None and medication.current_stock > medication.max_threshold
    if is_over and not medication.overstock_alerted:
        medication.overstock_alerted = True
        changed_fields.append("overstock_alerted")
        from .notifications import send_overstock_alert

        transaction.on_commit(lambda: send_overstock_alert(medication_id=medication.id))
    elif not is_over and medication.overstock_alerted:
        medication.overstock_alerted = False
        changed_fields.append("overstock_alerted")

    if changed_fields:
        medication.save(update_fields=changed_fields)


@transaction.atomic
def receive_stock_batch(
    *,
    medication,
    actor,
    batch_number,
    expiry_date,
    received_date,
    quantity_received,
    unit_cost=Decimal("0.00"),
    supplier="",
):
    """Achat / réception de stock — augmente le stock (demande initiale de la fonctionnalité)."""
    batch = StockBatch.objects.create(
        clinic=medication.clinic,
        medication=medication,
        batch_number=batch_number,
        expiry_date=expiry_date,
        received_date=received_date,
        quantity_received=quantity_received,
        quantity_remaining=quantity_received,
        unit_cost=unit_cost,
        supplier=supplier,
        created_by=actor,
    )
    StockMovement.objects.create(
        clinic=medication.clinic,
        medication=medication,
        batch=batch,
        movement_type=StockMovement.MovementType.PURCHASE,
        quantity_delta=quantity_received,
        created_by=actor,
    )
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=batch, metadata={"quantity": quantity_received})
    _recompute_current_stock(medication)
    return batch


@transaction.atomic
def adjust_stock(*, batch, actor, quantity_delta, reason):
    """Correction manuelle (ex. casse constatée à l'inventaire) — toujours rattachée à un lot
    précis pour que current_stock reste exactement la somme des lots (voir StockMovement.batch)."""
    if quantity_delta == 0:
        raise ValidationError("The adjustment quantity cannot be zero.")
    new_remaining = batch.quantity_remaining + quantity_delta
    if new_remaining < 0:
        raise ValidationError("This adjustment would make the batch's remaining quantity negative.")
    batch.quantity_remaining = new_remaining
    batch.save(update_fields=["quantity_remaining"])
    movement = StockMovement.objects.create(
        clinic=batch.clinic,
        medication=batch.medication,
        batch=batch,
        movement_type=StockMovement.MovementType.ADJUSTMENT,
        quantity_delta=quantity_delta,
        reason=reason,
        created_by=actor,
    )
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=movement, metadata={"reason": reason})
    _recompute_current_stock(batch.medication)
    return movement


def _dispense(*, medication, quantity, invoice, actor):
    """FEFO (first-expired-first-out) : consomme les lots dont la péremption est la plus proche
    en premier."""
    remaining_to_take = quantity
    batches = (
        medication.batches.select_for_update()
        .filter(quantity_remaining__gt=0)
        .order_by("expiry_date", "received_date")
    )
    for batch in batches:
        if remaining_to_take <= 0:
            break
        take = min(batch.quantity_remaining, remaining_to_take)
        batch.quantity_remaining -= take
        batch.save(update_fields=["quantity_remaining"])
        StockMovement.objects.create(
            clinic=medication.clinic,
            medication=medication,
            batch=batch,
            movement_type=StockMovement.MovementType.DISPENSE,
            quantity_delta=-take,
            invoice=invoice,
            created_by=actor,
        )
        remaining_to_take -= take

    if remaining_to_take > 0:
        raise ValidationError(
            f"Insufficient stock for {medication.name}: missing {remaining_to_take} {medication.unit}."
        )


def _return_to_stock(*, medication, quantity, invoice, actor):
    """Réconciliation inverse : annule les dispensations de cette facture pour ce médicament, en
    remontant du mouvement le plus récent vers le plus ancien, jusqu'à couvrir `quantity`."""
    remaining_to_return = quantity
    movements = (
        StockMovement.objects.filter(invoice=invoice, medication=medication)
        .values("batch_id")
        .annotate(net=Sum("quantity_delta"), last_at=Max("created_at"))
        .filter(net__lt=0)
        .order_by("-last_at")
    )
    for row in movements:
        if remaining_to_return <= 0:
            break
        available_to_return = -row["net"]
        give_back = min(available_to_return, remaining_to_return)
        batch = StockBatch.objects.select_for_update().get(pk=row["batch_id"])
        batch.quantity_remaining += give_back
        batch.save(update_fields=["quantity_remaining"])
        StockMovement.objects.create(
            clinic=medication.clinic,
            medication=medication,
            batch=batch,
            movement_type=StockMovement.MovementType.RETURN,
            quantity_delta=give_back,
            invoice=invoice,
            created_by=actor,
        )
        remaining_to_return -= give_back
    # remaining_to_return > 0 signifierait qu'on essaie de retourner plus que ce qui a jamais été
    # dispensé sur cette facture — ne peut pas arriver, sync_invoice_stock calcule le delta à
    # partir de ce même ledger (aucune saisie externe de quantité à retourner).


@transaction.atomic
def sync_invoice_stock(*, invoice, actor=None):
    """Point d'entrée unique appelé par billing/services.py (issue_invoice, update_invoice,
    cancel_invoice) : fait correspondre le stock réellement dispensé pour cette facture à ce que
    ses lignes médicament exigent actuellement, en se basant sur le ledger StockMovement comme
    source de vérité (pas de paramètre "quantité précédente" à faire circuler). Idempotent : si
    rien n'a changé, delta vaut 0 pour chaque médicament et aucun mouvement n'est créé."""
    desired_by_medication = {}
    if invoice.status in DISPENSING_STATUSES:
        for medication_id, total in (
            invoice.lines.filter(medication__isnull=False)
            .values("medication_id")
            .annotate(total=Sum("quantity"))
            .values_list("medication_id", "total")
        ):
            desired_by_medication[medication_id] = total

    already_dispensed = dict(
        StockMovement.objects.filter(
            invoice=invoice, movement_type__in=[StockMovement.MovementType.DISPENSE, StockMovement.MovementType.RETURN]
        )
        .values("medication_id")
        .annotate(net=Sum("quantity_delta"))
        .values_list("medication_id", "net")
    )

    medication_ids = set(desired_by_medication) | set(already_dispensed)
    for medication_id in medication_ids:
        medication = Medication.objects.select_for_update().get(pk=medication_id)
        desired_qty = desired_by_medication.get(medication_id, 0)
        dispensed_qty = -already_dispensed.get(medication_id, 0)
        delta = desired_qty - dispensed_qty
        if delta > 0:
            _dispense(medication=medication, quantity=delta, invoice=invoice, actor=actor)
        elif delta < 0:
            _return_to_stock(medication=medication, quantity=-delta, invoice=invoice, actor=actor)
        if delta != 0:
            _recompute_current_stock(medication)
