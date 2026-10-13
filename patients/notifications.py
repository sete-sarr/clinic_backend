"""Notification de l'accès d'urgence (business/notification-rules.md) : les administrateurs de la
clinique sont prévenus dans l'application. Aucune donnée médicale ni le motif dans le message — le
motif se lit dans le journal d'audit."""

from django.utils.translation import gettext as _

from communication.models import InAppNotification
from communication.services import notify_in_app


def notify_emergency_access(access):
    admins = access.clinic.users.filter(groups__name="clinic_admin", is_active=True).distinct()
    doctor = access.doctor.user.get_full_name() or access.doctor.user.get_username()
    number = access.patient.patient_number

    def compose():
        return (
            _("Accès d'urgence à un dossier patient"),
            _("%(doctor)s a ouvert un accès d'urgence de 24 h au dossier du patient %(number)s. Le motif figure dans le journal d'audit.")
            % {"doctor": doctor, "number": number},
        )

    notify_in_app(
        clinic=access.clinic,
        recipients=list(admins),
        category=InAppNotification.Category.SYSTEM,
        priority=InAppNotification.Priority.HIGH,
        compose=compose,
        link="/audit-log",
    )
