"""Logique métier du laboratoire (docs/laboratory.md). Les transitions de la demande passent
uniquement par ces fonctions (business/workflow-policy.md § WORKFLOW DEMANDE DE LABORATOIRE) :

    Demandé → Prélevé → En traitement → Terminé → Validé ; Demandé → Annulé.

Chaque transition verrouille la demande (select_for_update) pour qu'un double clic ou deux postes
ne puissent pas l'appliquer deux fois."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from common.audit import record_audit
from common.models import AuditLog, SequenceCounter

from .models import LabOrder, LabOrderItem, LabResult, LabResultFile, LabTest

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


# ---------------------------------------------------------------- catalogue


@transaction.atomic
def create_lab_test(*, clinic, actor, **fields):
    _check_unique_code(clinic=clinic, code=fields.get("code", ""))
    test = LabTest.objects.create(clinic=clinic, **fields)
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=test)
    return test


@transaction.atomic
def update_lab_test(*, test, actor, **fields):
    if "code" in fields:
        _check_unique_code(clinic=test.clinic, code=fields["code"], exclude_pk=test.pk)
    for key, value in fields.items():
        setattr(test, key, value)
    test.save()
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=test)
    return test


@transaction.atomic
def set_lab_test_active(*, test, actor, active):
    test.is_active = active
    test.archived_at = None if active else timezone.now()
    test.save(update_fields=["is_active", "archived_at", "updated_at"])
    action = AuditLog.Action.UPDATE if active else AuditLog.Action.ARCHIVE
    record_audit(user=actor, action=action, obj=test, metadata={"reason": "restored"} if active else None)
    return test


def _check_unique_code(*, clinic, code, exclude_pk=None):
    duplicates = LabTest.objects.filter(clinic=clinic, code__iexact=code.strip())
    if exclude_pk:
        duplicates = duplicates.exclude(pk=exclude_pk)
    if duplicates.exists():
        raise ValidationError(_("Un examen avec ce code existe déjà dans la clinique."))


# ---------------------------------------------------------------- résultats


def is_abnormal(value, reference_min, reference_max):
    """Hors norme : valeur numérique hors des valeurs de référence. Une valeur qualitative
    (« négatif », « présence »…) n'est jamais signalée automatiquement."""
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return False
    if not number.is_finite():
        return False
    return (reference_min is not None and number < reference_min) or (
        reference_max is not None and number > reference_max
    )


def _current_result(item):
    return item.results.filter(superseded_at__isnull=True).first()


# ---------------------------------------------------------------- demandes


def _generate_order_number(clinic):
    year = timezone.now().year
    sequence = SequenceCounter.next_value(clinic=clinic, key="lab_order_number", year=year)
    return f"LAB-{year}-{sequence:05d}"


@transaction.atomic
def create_lab_order(*, clinic, doctor, patient, tests, actor, consultation=None, clinical_note=""):
    # Messages volontairement génériques : pas d'oracle d'existence inter-cliniques (docs/security.md).
    if patient.clinic_id != clinic.id:
        raise ValidationError(_("Patient invalide."))
    if doctor.clinic_id != clinic.id:
        raise ValidationError(_("Médecin invalide."))
    if consultation is not None and (consultation.clinic_id != clinic.id or consultation.patient_id != patient.id):
        raise ValidationError(_("Consultation invalide."))
    if not tests:
        raise ValidationError(_("Une demande doit contenir au moins un examen."))
    if len({test.pk for test in tests}) != len(tests):
        raise ValidationError(_("Un même examen ne peut pas être demandé deux fois."))
    if any(test.clinic_id != clinic.id or not test.is_active for test in tests):
        raise ValidationError(_("Examen invalide."))

    order = LabOrder.objects.create(
        clinic=clinic,
        number=_generate_order_number(clinic),
        patient=patient,
        doctor=doctor,
        consultation=consultation,
        clinical_note=clinical_note,
    )
    LabOrderItem.objects.bulk_create([
        LabOrderItem(
            order=order, test=test, test_code=test.code, test_name=test.name, unit=test.unit,
            reference_min=test.reference_min, reference_max=test.reference_max, price=test.price,
        )
        for test in tests
    ])
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=order)

    from .notifications import notify_order_created

    notify_order_created(order)
    return order


def _locked(order, allowed_statuses, message):
    order = LabOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in allowed_statuses:
        raise ValidationError(message)
    return order


@transaction.atomic
def collect_sample(*, order, actor):
    order = _locked(order, {LabOrder.Status.REQUESTED}, _("Seule une demande au statut « Demandé » peut être prélevée."))
    order.status = LabOrder.Status.COLLECTED
    order.collected_at = timezone.now()
    order.collected_by = actor
    order.save(update_fields=["status", "collected_at", "collected_by", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=order, metadata={"status": order.status})
    return order


@transaction.atomic
def start_processing(*, order, actor):
    order = _locked(order, {LabOrder.Status.COLLECTED}, _("Seule une demande prélevée peut passer en traitement."))
    order.status = LabOrder.Status.IN_PROGRESS
    order.save(update_fields=["status", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=order, metadata={"status": order.status})
    return order


def _item_of(order, item_id):
    item = order.items.filter(pk=item_id).first()
    if item is None:
        raise ValidationError(_("Examen invalide."))
    return item


@transaction.atomic
def record_results(*, order, actor, entries):
    """Saisie (ou correction avant validation) des résultats pendant « En traitement ».
    `entries` : [{"item": id, "value": str, "comment": str}]. Le drapeau hors norme est recalculé ;
    un passage à « hors norme » alerte le médecin prescripteur (notification-rules.md)."""
    order = _locked(
        order, {LabOrder.Status.IN_PROGRESS}, _("Les résultats ne peuvent être saisis que pendant le traitement.")
    )
    newly_abnormal = []
    for entry in entries:
        item = _item_of(order, entry["item"])
        value = (entry.get("value") or "").strip()
        abnormal = is_abnormal(value, item.reference_min, item.reference_max)
        result = _current_result(item)
        was_abnormal = bool(result and result.is_abnormal)
        if result is None:
            result = LabResult(item=item)
        result.value = value
        result.comment = entry.get("comment") or ""
        result.is_abnormal = abnormal
        result.entered_by = actor
        result.save()
        if abnormal and not was_abnormal:
            newly_abnormal.append(item)
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=order, metadata={"results": len(entries)})

    if newly_abnormal:
        from .notifications import notify_abnormal_results

        notify_abnormal_results(order, newly_abnormal)
    return order


@transaction.atomic
def attach_result_file(*, order, item_id, actor, uploaded_file):
    order = _locked(
        order, {LabOrder.Status.IN_PROGRESS}, _("Les résultats ne peuvent être saisis que pendant le traitement.")
    )
    item = _item_of(order, item_id)
    content = uploaded_file.read()
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValidationError(_("Le fichier ne doit pas dépasser 5 Mo."))
    if not content.startswith(b"%PDF-"):
        raise ValidationError(_("Seuls les fichiers PDF sont acceptés."))
    result = _current_result(item) or LabResult.objects.create(item=item, entered_by=actor)
    LabResultFile.objects.update_or_create(
        result=result,
        defaults={"filename": (uploaded_file.name or "resultat.pdf")[:200], "content": content, "size": len(content)},
    )
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=order, metadata={"attachment": item.test_code})
    return order


def _has_result(item):
    result = _current_result(item)
    return result is not None and (bool(result.value) or hasattr(result, "attachment"))


@transaction.atomic
def complete_order(*, order, actor):
    order = _locked(order, {LabOrder.Status.IN_PROGRESS}, _("Seule une demande en traitement peut être terminée."))
    if not all(_has_result(item) for item in order.items.all()):
        raise ValidationError(_("Chaque examen demandé doit avoir un résultat (valeur ou PDF) avant de terminer."))
    order.status = LabOrder.Status.COMPLETED
    order.completed_at = timezone.now()
    order.save(update_fields=["status", "completed_at", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=order, metadata={"status": order.status})

    from .notifications import notify_results_ready

    notify_results_ready(order)
    return order


@transaction.atomic
def validate_order(*, order, actor):
    """Validation médicale par le médecin prescripteur : les résultats deviennent immuables et
    visibles du patient ; les examens sont ajoutés à une nouvelle facture brouillon (décision de
    l'utilisateur du 2026-10-07), dont les totaux et la TVA restent calculés par `billing`."""
    order = _locked(order, {LabOrder.Status.COMPLETED}, _("Seule une demande terminée peut être validée."))
    if getattr(getattr(actor, "doctor_profile", None), "pk", None) != order.doctor_id:
        raise ValidationError(_("Seul le médecin prescripteur peut valider ces résultats."))

    from billing.services import create_invoice
    from communication.services import language_for_clinic

    items = list(order.items.all())
    with translation.override(language_for_clinic(order.clinic)):
        lines = [
            {
                "description": _("Examen de laboratoire : %(name)s (%(code)s)") % {"name": item.test_name, "code": item.test_code},
                "quantity": 1,
                "unit_price": item.price,
            }
            for item in items
        ]
    order.invoice = create_invoice(clinic=order.clinic, patient=order.patient, doctor=order.doctor, lines=lines)
    order.status = LabOrder.Status.VALIDATED
    order.validated_at = timezone.now()
    order.validated_by = actor
    order.save(update_fields=["status", "validated_at", "validated_by", "invoice", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=order, metadata={"status": order.status})
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=order.invoice, metadata={"lab_order": order.number})

    from .notifications import notify_patient_results_available

    notify_patient_results_available(order)
    return order


@transaction.atomic
def cancel_order(*, order, actor):
    order = _locked(order, {LabOrder.Status.REQUESTED}, _("Seule une demande non encore prélevée peut être annulée."))
    order.status = LabOrder.Status.CANCELLED
    order.cancelled_at = timezone.now()
    order.save(update_fields=["status", "cancelled_at", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.CANCEL, obj=order)
    return order


@transaction.atomic
def correct_result(*, order, item_id, actor, value, comment, reason):
    """Correction d'un résultat déjà validé : jamais de modification, un nouveau résultat motivé
    remplace l'ancien, conservé dans l'historique (docs/laboratory.md §8)."""
    order = _locked(order, {LabOrder.Status.VALIDATED}, _("Seul un résultat validé se corrige par ce moyen."))
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(_("Le motif de la correction est obligatoire."))
    value = (value or "").strip()
    if not value:
        raise ValidationError(_("La valeur corrigée est obligatoire."))
    item = _item_of(order, item_id)
    previous = _current_result(item)
    if previous is not None:
        previous.superseded_at = timezone.now()
        previous.save(update_fields=["superseded_at"])
    abnormal = is_abnormal(value, item.reference_min, item.reference_max)
    LabResult.objects.create(
        item=item, value=value, comment=comment or "", is_abnormal=abnormal, entered_by=actor, correction_reason=reason,
    )
    record_audit(
        user=actor, action=AuditLog.Action.UPDATE, obj=order,
        metadata={"correction": item.test_code, "previous": previous.value if previous else "", "reason": reason},
    )
    if abnormal:
        from .notifications import notify_abnormal_results

        notify_abnormal_results(order, [item])
    return order
