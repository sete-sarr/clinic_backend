"""Indicateurs graphiques du tableau de bord (business/reporting-export-policy.md § Tableau de bord).

Agrégats calculés en base sur les valeurs persistées (Invoice.total_amount, Payment.amount) — jamais
recalculés à partir des lignes (docs/reporting-guidelines.md). Toujours limités à la clinique de
l'utilisateur, et par rôle :
- rendez-vous (30 derniers jours) : administrateur, secrétaire, médecin (ses seuls rendez-vous) ;
- encaissements (6 derniers mois) et factures par statut : administrateur et comptable uniquement.

Montants : seulement ceux de la devise actuelle de la clinique (une facture garde la devise de sa
création, docs/i18n.md §8 — additionner deux devises n'aurait pas de sens)."""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth

from appointments.models import Appointment
from billing.models import Invoice
from common.permissions import in_role
from payments.models import Payment

APPOINTMENT_WINDOW_DAYS = 30
REVENUE_WINDOW_MONTHS = 6
APPOINTMENT_OUTCOMES = (Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW, Appointment.Status.CANCELLED)
OPEN_INVOICE_STATUSES = (Invoice.Status.ISSUED, Invoice.Status.PENDING_PAYMENT)
CHARTED_INVOICE_STATUSES = (Invoice.Status.PAID, Invoice.Status.PENDING_PAYMENT, Invoice.Status.ISSUED)


def can_see_appointment_stats(user):
    return in_role(user, "clinic_admin", "secretary", "doctor")


def can_see_financial_stats(user):
    return in_role(user, "clinic_admin", "accountant")


def _first_day_months_ago(today, months):
    year, month = today.year, today.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _amount(value):
    return str((value or Decimal("0")).quantize(Decimal("0.01")))


def appointment_stats(*, user, clinic, today):
    """Issue des rendez-vous jour par jour sur les 30 derniers jours (aujourd'hui compris)."""
    start = today - timedelta(days=APPOINTMENT_WINDOW_DAYS - 1)
    appointments = Appointment.objects.filter(
        clinic=clinic, date__range=(start, today), status__in=APPOINTMENT_OUTCOMES
    )
    # Même règle que la liste des rendez-vous : un médecin (sans autre rôle) ne voit que les siens.
    if in_role(user, "doctor") and not in_role(user, "clinic_admin", "secretary"):
        appointments = appointments.filter(doctor=getattr(user, "doctor_profile", None))

    counts = {}
    for row in appointments.values("date", "status").annotate(n=Count("id")):
        counts[(row["date"], row["status"])] = row["n"]

    days, totals = [], {status: 0 for status in APPOINTMENT_OUTCOMES}
    for offset in range(APPOINTMENT_WINDOW_DAYS):
        day = start + timedelta(days=offset)
        entry = {"date": day.isoformat()}
        for status in APPOINTMENT_OUTCOMES:
            entry[status] = counts.get((day, status), 0)
            totals[status] += entry[status]
        days.append(entry)

    attended = totals[Appointment.Status.COMPLETED] + totals[Appointment.Status.NO_SHOW]
    no_show_rate = round(totals[Appointment.Status.NO_SHOW] / attended, 4) if attended else None
    return {"date_from": start.isoformat(), "date_to": today.isoformat(), "days": days, "totals": totals,
            "no_show_rate": no_show_rate}


def revenue_stats(*, clinic, today):
    """Paiements validés (remboursements exclus) par mois sur les 6 derniers mois, mois en cours compris."""
    start = _first_day_months_ago(today, REVENUE_WINDOW_MONTHS - 1)
    rows = (
        Payment.objects.filter(
            clinic=clinic, status=Payment.Status.VALIDATED, date__range=(start, today),
            invoice__currency=clinic.currency,
        )
        .annotate(month=TruncMonth("date"))
        .values("month")
        .annotate(total=Sum("amount"))
    )
    by_month = {(row["month"].year, row["month"].month): row["total"] for row in rows}

    months, grand_total = [], Decimal("0")
    for offset in range(REVENUE_WINDOW_MONTHS):
        month_start = _first_day_months_ago(today, REVENUE_WINDOW_MONTHS - 1 - offset)
        total = by_month.get((month_start.year, month_start.month), Decimal("0"))
        grand_total += total
        months.append({"month": month_start.strftime("%Y-%m"), "amount": _amount(total)})
    return {"date_from": start.isoformat(), "date_to": today.isoformat(), "months": months,
            "total": _amount(grand_total)}


def invoice_status_stats(*, clinic, today):
    """Factures émises sur les 6 derniers mois, par statut (nombre et montant), et reste à encaisser."""
    start = _first_day_months_ago(today, REVENUE_WINDOW_MONTHS - 1)
    invoices = Invoice.objects.filter(
        clinic=clinic, issue_date__range=(start, today), currency=clinic.currency,
        status__in=CHARTED_INVOICE_STATUSES,
    )
    by_status = {row["status"]: row for row in invoices.values("status").annotate(n=Count("id"), amount=Sum("total_amount"))}
    open_invoices = invoices.filter(status__in=OPEN_INVOICE_STATUSES)
    open_total = open_invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    # Même calcul que Invoice.balance_due : total persisté moins les paiements validés.
    open_paid = Payment.objects.filter(
        invoice__in=open_invoices, status=Payment.Status.VALIDATED
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    statuses = [
        {"status": status, "count": by_status.get(status, {}).get("n", 0),
         "amount": _amount(by_status.get(status, {}).get("amount"))}
        for status in CHARTED_INVOICE_STATUSES
    ]
    return {"date_from": start.isoformat(), "date_to": today.isoformat(), "statuses": statuses,
            "balance_due": _amount(open_total - open_paid)}


def dashboard_stats(*, user, clinic, today):
    """Sections autorisées pour ce rôle ; None pour les autres."""
    financial = can_see_financial_stats(user)
    return {
        "currency": clinic.currency,
        "appointments": appointment_stats(user=user, clinic=clinic, today=today) if can_see_appointment_stats(user) else None,
        "revenue": revenue_stats(clinic=clinic, today=today) if financial else None,
        "invoices": invoice_status_stats(clinic=clinic, today=today) if financial else None,
    }
