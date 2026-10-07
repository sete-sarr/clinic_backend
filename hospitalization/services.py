"""Logique métier de l'hospitalisation (docs/hospitalization.md). Chaque transition verrouille le
séjour et les lits concernés (select_for_update) dans une transaction : un lit n'est jamais attribué
deux fois, même si deux postes valident en même temps.

    Séjour : Planifié → Admis → Sorti ; Planifié → Annulé.
    Lit    : Libre → Occupé → En nettoyage → Libre ; Libre / En nettoyage ↔ Hors service."""

from collections import Counter
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from common.audit import record_audit
from common.models import AuditLog, SequenceCounter

from .models import Admission, Bed, BedTransfer, NursingNote, VitalSign

# Bornes physiologiques plausibles (business/validation-rules.md § VALIDATION HOSPITALISATION) :
# une valeur hors bornes est une erreur de saisie, refusée.
VITAL_RANGES = {
    "temperature": (Decimal("30"), Decimal("45")),
    "systolic": (40, 300),
    "diastolic": (20, 200),
    "pulse": (20, 250),
    "respiratory_rate": (4, 80),
    "oxygen_saturation": (0, 100),
    "weight": (Decimal("0.3"), Decimal("500")),
    "pain": (0, 10),
}


# ---------------------------------------------------------------- lits


def _locked_bed(bed):
    return Bed.objects.select_for_update().get(pk=bed.pk)


@transaction.atomic
def mark_bed_clean(*, bed, actor):
    bed = _locked_bed(bed)
    if bed.status != Bed.Status.CLEANING:
        raise ValidationError(_("Seul un lit en nettoyage peut être marqué comme nettoyé."))
    return _set_bed_status(bed, Bed.Status.FREE, actor)


@transaction.atomic
def set_bed_out_of_service(*, bed, actor):
    bed = _locked_bed(bed)
    if bed.status not in (Bed.Status.FREE, Bed.Status.CLEANING):
        raise ValidationError(_("Un lit occupé ne peut pas être mis hors service."))
    return _set_bed_status(bed, Bed.Status.OUT_OF_SERVICE, actor)


@transaction.atomic
def put_bed_back_in_service(*, bed, actor):
    bed = _locked_bed(bed)
    if bed.status != Bed.Status.OUT_OF_SERVICE:
        raise ValidationError(_("Ce lit n'est pas hors service."))
    return _set_bed_status(bed, Bed.Status.FREE, actor)


@transaction.atomic
def set_bed_active(*, bed, actor, active):
    bed = _locked_bed(bed)
    if not active and bed.status == Bed.Status.OCCUPIED:
        raise ValidationError(_("Un lit occupé ne peut pas être archivé."))
    bed.is_active = active
    bed.archived_at = None if active else timezone.now()
    bed.save(update_fields=["is_active", "archived_at", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE if active else AuditLog.Action.ARCHIVE, obj=bed)
    return bed


@transaction.atomic
def set_room_active(*, room, actor, active):
    if not active and room.beds.filter(status=Bed.Status.OCCUPIED).exists():
        raise ValidationError(_("Une chambre dont un lit est occupé ne peut pas être archivée."))
    room.is_active = active
    room.archived_at = None if active else timezone.now()
    room.save(update_fields=["is_active", "archived_at", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE if active else AuditLog.Action.ARCHIVE, obj=room)
    return room


def _set_bed_status(bed, status, actor):
    bed.status = status
    bed.save(update_fields=["status", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=bed, metadata={"status": status})
    return bed


def _take_free_bed(bed, clinic):
    bed = _locked_bed(bed)
    if bed.clinic_id != clinic.id or not bed.is_active or not bed.room.is_active:
        raise ValidationError(_("Lit invalide."))
    if bed.status != Bed.Status.FREE:
        raise ValidationError(_("Ce lit n'est pas libre."))
    bed.status = Bed.Status.OCCUPIED
    bed.save(update_fields=["status", "updated_at"])
    return bed


def _release_bed(bed):
    bed = _locked_bed(bed)
    bed.status = Bed.Status.CLEANING
    bed.save(update_fields=["status", "updated_at"])
    return bed


# ---------------------------------------------------------------- séjours


def _check_billing_configured(clinic):
    """Un tarif doit exister avant d'admettre : la sortie, qui facture les nuitées dans la même
    transaction, ne doit jamais échouer faute de tarif (et aucun tarif n'est inventé)."""
    if clinic.inpatient_billing_mode == clinic.InpatientBillingMode.FLAT and not clinic.inpatient_nightly_rate:
        raise ValidationError(
            _("Le forfait de nuitée n'est pas configuré : l'administrateur doit le renseigner dans Paramètres.")
        )


def _locked_admission(admission, allowed, message):
    admission = Admission.objects.select_for_update().get(pk=admission.pk)
    if admission.status not in allowed:
        raise ValidationError(message)
    return admission


@transaction.atomic
def create_admission(*, clinic, doctor, patient, department, reason, actor, planned_for=None, bed=None):
    # Messages volontairement génériques : pas d'oracle d'existence inter-cliniques.
    if patient.clinic_id != clinic.id:
        raise ValidationError(_("Patient invalide."))
    if doctor.clinic_id != clinic.id:
        raise ValidationError(_("Médecin invalide."))
    if department.clinic_id != clinic.id or not department.is_active:
        raise ValidationError(_("Service invalide."))
    if not (reason or "").strip():
        raise ValidationError(_("Le motif d'hospitalisation est obligatoire."))
    year = timezone.now().year
    sequence = SequenceCounter.next_value(clinic=clinic, key="admission_number", year=year)
    admission = Admission.objects.create(
        clinic=clinic, number=f"HOS-{year}-{sequence:05d}", patient=patient, doctor=doctor,
        department=department, reason=reason.strip(), planned_for=planned_for,
    )
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=admission)
    if bed is not None:
        admission = admit(admission=admission, bed=bed, actor=actor)
    return admission


@transaction.atomic
def admit(*, admission, bed, actor):
    admission = _locked_admission(
        admission, {Admission.Status.PLANNED}, _("Seul un séjour planifié peut être admis.")
    )
    _check_billing_configured(admission.clinic)
    if Admission.objects.filter(patient_id=admission.patient_id, status=Admission.Status.ADMITTED).exists():
        raise ValidationError(_("Ce patient est déjà hospitalisé."))
    bed = _take_free_bed(bed, admission.clinic)
    now = timezone.now()
    admission.bed = bed
    admission.status = Admission.Status.ADMITTED
    admission.admitted_at = now
    admission.admitted_by = actor
    admission.save(update_fields=["bed", "status", "admitted_at", "admitted_by", "updated_at"])
    BedTransfer.objects.create(admission=admission, to_bed=bed, transferred_at=now, transferred_by=actor)
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=admission, metadata={"status": admission.status, "bed": str(bed)})

    from .notifications import notify_admission

    notify_admission(admission)
    return admission


@transaction.atomic
def transfer(*, admission, bed, actor, reason=""):
    admission = _locked_admission(
        admission, {Admission.Status.ADMITTED}, _("Seul un patient hospitalisé peut changer de lit.")
    )
    if bed.pk == admission.bed_id:
        raise ValidationError(_("Le patient occupe déjà ce lit."))
    new_bed = _take_free_bed(bed, admission.clinic)
    old_bed = _release_bed(admission.bed)
    admission.bed = new_bed
    admission.save(update_fields=["bed", "updated_at"])
    BedTransfer.objects.create(
        admission=admission, from_bed=old_bed, to_bed=new_bed, transferred_at=timezone.now(),
        transferred_by=actor, reason=(reason or "")[:255],
    )
    record_audit(
        user=actor, action=AuditLog.Action.UPDATE, obj=admission,
        metadata={"transfer": f"{old_bed} -> {new_bed}"},
    )
    return admission


def _midnights(start, end):
    """Minuits (heure locale de la clinique) franchis entre `start` et `end` : une nuitée chacun
    (décision de l'utilisateur du 2026-10-07)."""
    current_tz = timezone.get_current_timezone()
    day = timezone.localtime(start, current_tz).date() + timedelta(days=1)
    last = timezone.localtime(end, current_tz).date()
    while day <= last:
        yield timezone.make_aware(datetime.combine(day, time.min), current_tz)
        day += timedelta(days=1)


def night_lines(admission, until):
    """Lignes de facture des nuitées, selon le mode de tarification de la clinique au moment de la
    sortie. Tarif figé sur la ligne. En mode « par type de chambre », la nuit compte pour le lit
    occupé à minuit (historique BedTransfer)."""
    clinic = admission.clinic
    midnights = list(_midnights(admission.admitted_at, until))
    if not midnights:
        return 0, []
    if clinic.inpatient_billing_mode == clinic.InpatientBillingMode.FLAT:
        _check_billing_configured(clinic)
        return len(midnights), [
            {"description": _("Nuitées d'hospitalisation"), "quantity": len(midnights), "unit_price": clinic.inpatient_nightly_rate}
        ]
    transfers = list(admission.transfers.select_related("to_bed__room__room_type"))
    per_type = Counter()
    room_types = {}
    for midnight in midnights:
        current = [t for t in transfers if t.transferred_at <= midnight][-1]
        room_type = current.to_bed.room.room_type
        room_types[room_type.pk] = room_type
        per_type[room_type.pk] += 1
    return len(midnights), [
        {
            "description": _("Nuitées d'hospitalisation — %(type)s") % {"type": room_types[pk].name},
            "quantity": count,
            "unit_price": room_types[pk].nightly_rate,
        }
        for pk, count in per_type.items()
    ]


@transaction.atomic
def discharge(*, admission, actor, summary=""):
    """Sortie prononcée par un médecin : libère le lit (« En nettoyage ») et facture les nuitées
    dans une nouvelle facture brouillon, dans la même transaction (workflow-policy.md)."""
    admission = _locked_admission(
        admission, {Admission.Status.ADMITTED}, _("Seul un patient hospitalisé peut sortir.")
    )
    now = timezone.now()
    from billing.services import create_invoice
    from communication.services import language_for_clinic

    with translation.override(language_for_clinic(admission.clinic)):
        nights, lines = night_lines(admission, now)
    if lines:
        admission.invoice = create_invoice(
            clinic=admission.clinic, patient=admission.patient, doctor=admission.doctor, lines=lines
        )
        record_audit(user=actor, action=AuditLog.Action.CREATE, obj=admission.invoice, metadata={"admission": admission.number})
    _release_bed(admission.bed)
    admission.status = Admission.Status.DISCHARGED
    admission.discharged_at = now
    admission.discharged_by = actor
    admission.discharge_summary = (summary or "").strip()
    admission.nights = nights
    admission.save(update_fields=[
        "status", "discharged_at", "discharged_by", "discharge_summary", "nights", "invoice", "updated_at",
    ])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=admission, metadata={"status": admission.status, "nights": nights})

    from .notifications import notify_discharge

    notify_discharge(admission)
    return admission


@transaction.atomic
def cancel_admission(*, admission, actor):
    admission = _locked_admission(
        admission, {Admission.Status.PLANNED}, _("Seul un séjour planifié, non commencé, peut être annulé.")
    )
    admission.status = Admission.Status.CANCELLED
    admission.cancelled_at = timezone.now()
    admission.save(update_fields=["status", "cancelled_at", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.CANCEL, obj=admission)
    return admission


# ---------------------------------------------------------------- soins


def _require_admitted(admission):
    if admission.status != Admission.Status.ADMITTED:
        raise ValidationError(_("Les soins ne peuvent être saisis que pendant l'hospitalisation."))


@transaction.atomic
def record_vital_signs(*, admission, actor, recorded_at=None, **values):
    _require_admitted(admission)
    measures = {field: value for field, value in values.items() if field in VITAL_RANGES and value is not None}
    if not measures:
        raise ValidationError(_("Saisissez au moins une constante."))
    for field, value in measures.items():
        low, high = VITAL_RANGES[field]
        if not (low <= value <= high):
            raise ValidationError({field: _("Valeur hors des bornes plausibles (%(low)s – %(high)s).") % {"low": low, "high": high}})
    now = timezone.now()
    recorded_at = recorded_at or now
    if recorded_at > now or recorded_at < admission.admitted_at:
        raise ValidationError({"recorded_at": _("L'heure de mesure doit se situer pendant l'hospitalisation.")})
    vital = VitalSign.objects.create(admission=admission, recorded_at=recorded_at, recorded_by=actor, **measures)
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=vital, metadata={"admission": admission.number})
    return vital


@transaction.atomic
def add_nursing_note(*, admission, actor, note):
    _require_admitted(admission)
    if not (note or "").strip():
        raise ValidationError(_("La note de soins ne peut pas être vide."))
    nursing_note = NursingNote.objects.create(admission=admission, note=note.strip(), recorded_by=actor)
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=nursing_note, metadata={"admission": admission.number})
    return nursing_note
