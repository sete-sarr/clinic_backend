"""Logique du registre des visiteurs (docs/visitors.md §5) : entrée, sortie, correction le jour même,
clôture automatique des visites oubliées et purge au-delà de la durée de conservation."""

from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from common.audit import record_audit
from common.models import AuditLog

from .models import VisitLog

# Conservation : 1 an (décision de l'utilisateur du 2026-10-07), puis suppression automatique.
RETENTION_DAYS = 365
EDITABLE_FIELDS = ("visitor_name", "visitor_phone", "visitor_type", "purpose",
                   "visited_admission", "visited_staff", "visited_free_text")


def _clean_target(*, clinic, visited_admission=None, visited_staff=None, visited_free_text=""):
    """La personne visitée, si elle est renseignée, appartient à la clinique : séjour Admis ou membre
    du personnel actif (business/validation-rules.md). Un seul des trois champs."""
    visited_free_text = (visited_free_text or "").strip()
    if sum(bool(value) for value in (visited_admission, visited_staff, visited_free_text)) > 1:
        raise ValidationError(_("Indiquez une seule personne visitée."))
    if visited_admission is not None and (
        visited_admission.clinic_id != clinic.id or visited_admission.status != "admitted"
    ):
        raise ValidationError(_("Ce patient n'est pas hospitalisé dans la clinique."))
    if visited_staff is not None and (
        visited_staff.clinic_id != clinic.id or not visited_staff.is_active or visited_staff.groups.filter(name="patient").exists()
    ):
        raise ValidationError(_("Membre du personnel invalide."))
    return {"visited_admission": visited_admission, "visited_staff": visited_staff, "visited_free_text": visited_free_text}


def _check_not_already_present(*, clinic, visitor_name, visitor_phone, exclude_pk=None):
    """Une même personne (même nom, même téléphone s'il est connu) n'est pas deux fois présente."""
    present = VisitLog.objects.filter(clinic=clinic, checked_out_at__isnull=True, visitor_name__iexact=visitor_name)
    if visitor_phone:
        present = present.filter(visitor_phone=visitor_phone)
    if exclude_pk:
        present = present.exclude(pk=exclude_pk)
    if present.exists():
        raise ValidationError(_("Cette personne est déjà enregistrée comme présente."))


@transaction.atomic
def check_in(*, clinic, actor, visitor_name, visitor_type, purpose, visitor_phone="", **target):
    visitor_name = (visitor_name or "").strip()
    purpose = (purpose or "").strip()
    visitor_phone = (visitor_phone or "").strip()
    if not visitor_name:
        raise ValidationError(_("Le nom du visiteur est obligatoire."))
    if not purpose:
        raise ValidationError(_("Le motif de la visite est obligatoire."))
    _check_not_already_present(clinic=clinic, visitor_name=visitor_name, visitor_phone=visitor_phone)
    visit = VisitLog.objects.create(
        clinic=clinic, visitor_name=visitor_name, visitor_phone=visitor_phone, visitor_type=visitor_type,
        purpose=purpose, checked_in_at=timezone.now(), checked_in_by=actor,
        **_clean_target(clinic=clinic, **target),
    )
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=visit)
    return visit


@transaction.atomic
def check_out(*, visit, actor):
    visit = VisitLog.objects.select_for_update().get(pk=visit.pk)
    if visit.checked_out_at is not None:
        raise ValidationError(_("La sortie de ce visiteur est déjà enregistrée."))
    visit.checked_out_at = max(timezone.now(), visit.checked_in_at + timedelta(seconds=1))
    visit.checked_out_by = actor
    visit.save(update_fields=["checked_out_at", "checked_out_by", "updated_at"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=visit, metadata={"checked_out": True})
    return visit


@transaction.atomic
def update_visit(*, visit, actor, **fields):
    """Correction d'une saisie, uniquement le jour même (permissions-matrix.md § REGISTRE)."""
    if timezone.localdate(visit.checked_in_at) != timezone.localdate():
        raise ValidationError(_("Une visite ne peut être corrigée que le jour même."))
    values = {field: fields.get(field, getattr(visit, field)) for field in EDITABLE_FIELDS}
    values["visitor_name"] = (values["visitor_name"] or "").strip()
    values["purpose"] = (values["purpose"] or "").strip()
    if not values["visitor_name"] or not values["purpose"]:
        raise ValidationError(_("Le nom du visiteur et le motif sont obligatoires."))
    if visit.checked_out_at is None:
        _check_not_already_present(
            clinic=visit.clinic, visitor_name=values["visitor_name"], visitor_phone=values["visitor_phone"], exclude_pk=visit.pk,
        )
    values.update(_clean_target(
        clinic=visit.clinic, visited_admission=values["visited_admission"], visited_staff=values["visited_staff"],
        visited_free_text=values["visited_free_text"],
    ))
    for field, value in values.items():
        setattr(visit, field, value)
    visit.save()
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=visit)
    return visit


def auto_close_open_visits(now=None):
    """Clôture, avec la mention « sortie non enregistrée », les visites ouvertes des jours précédents :
    heure de sortie = fin de leur journée d'entrée. Renvoie le nombre de visites clôturées."""
    now = now or timezone.now()
    current_tz = timezone.get_current_timezone()
    today_start = timezone.make_aware(datetime.combine(timezone.localdate(now), time.min), current_tz)
    closed = 0
    for visit in VisitLog.objects.filter(checked_out_at__isnull=True, checked_in_at__lt=today_start):
        day = timezone.localdate(visit.checked_in_at)
        visit.checked_out_at = timezone.make_aware(datetime.combine(day, time(23, 59, 59)), current_tz)
        visit.auto_closed = True
        visit.save(update_fields=["checked_out_at", "auto_closed", "updated_at"])
        closed += 1
    return closed


def purge_expired_visits(now=None):
    """Suppression définitive au-delà de la durée de conservation : le registre n'est pas une donnée
    de santé et ne doit pas être conservé comme telle (docs/visitors.md §6)."""
    limit = (now or timezone.now()) - timedelta(days=RETENTION_DAYS)
    deleted, _details = VisitLog.objects.filter(checked_in_at__lt=limit).delete()
    return deleted
