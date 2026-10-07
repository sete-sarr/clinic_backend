"""Notifications in-app de l'hospitalisation (business/notification-rules.md) :

- ADMISSION → médecin référent et infirmiers de la clinique, priorité haute ;
- SORTIE → réception et comptabilité (nuitées ajoutées à la facture), priorité moyenne.

Jamais le motif médical : le nom du patient et son emplacement suffisent à l'accueil comme au soin."""

from django.utils.translation import gettext as _

from communication.models import InAppNotification
from communication.services import notify_in_app


def _patient_name(admission):
    return f"{admission.patient.first_name} {admission.patient.last_name}".strip()


def _location(admission):
    bed = admission.bed
    return _("%(department)s, chambre %(room)s, lit %(bed)s") % {
        "department": admission.department.name, "room": bed.room.number, "bed": bed.label,
    }


def notify_admission(admission):
    nurses = admission.clinic.users.filter(groups__name="nurse", is_active=True).distinct()

    def compose():
        return (
            _("Admission : %(patient)s") % {"patient": _patient_name(admission)},
            _("%(number)s — %(location)s.") % {"number": admission.number, "location": _location(admission)},
        )

    notify_in_app(
        clinic=admission.clinic, recipients=[admission.doctor.user, *nurses],
        category=InAppNotification.Category.HOSPITALIZATION, priority=InAppNotification.Priority.HIGH,
        compose=compose, link=f"/hospitalization/stays/{admission.pk}",
    )


def notify_discharge(admission):
    recipients = admission.clinic.users.filter(groups__name__in=["secretary", "accountant"], is_active=True).distinct()

    def compose():
        body = _("%(number)s — %(nights)s nuitée(s).") % {"number": admission.number, "nights": admission.nights}
        if admission.invoice_id:
            body = _("%(number)s — %(nights)s nuitée(s), facture brouillon %(invoice)s.") % {
                "number": admission.number, "nights": admission.nights, "invoice": admission.invoice.number,
            }
        return _("Sortie d'hospitalisation : %(patient)s") % {"patient": _patient_name(admission)}, body

    notify_in_app(
        clinic=admission.clinic, recipients=recipients,
        category=InAppNotification.Category.HOSPITALIZATION, priority=InAppNotification.Priority.MEDIUM,
        compose=compose, link=f"/hospitalization/stays/{admission.pk}",
    )
