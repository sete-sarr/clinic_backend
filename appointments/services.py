from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy

from common.models import SequenceCounter

from .models import Appointment

ACTIVE_STATUSES = [Appointment.Status.PENDING, Appointment.Status.CONFIRMED]
TERMINAL_STATUSES = {Appointment.Status.CANCELLED, Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW}
PATIENT_CANCELLABLE_STATUSES = [Appointment.Status.PENDING, Appointment.Status.CONFIRMED]

# Libellés français pour les messages d'erreur — les labels de Appointment.Status restent en anglais
# (les changer imposerait une migration) ; même vocabulaire que APPOINTMENT_STATUS_LABELS côté frontend.
_STATUS_LABELS_FR = {
    Appointment.Status.PENDING: gettext_lazy("en attente"),
    Appointment.Status.CONFIRMED: gettext_lazy("confirmé"),
    Appointment.Status.COMPLETED: gettext_lazy("terminé"),
    Appointment.Status.CANCELLED: gettext_lazy("annulé"),
    Appointment.Status.NO_SHOW: gettext_lazy("marqué absent"),
}


def _status_label(appointment):
    return _STATUS_LABELS_FR.get(appointment.status, appointment.status)


def validate_not_past(*, date, time):
    now = timezone.localtime(timezone.now())
    if date < now.date() or (date == now.date() and time < now.time()):
        raise ValidationError(_("Impossible de programmer un rendez-vous dans le passé."))


def appointment_doctor_display(appointment):
    """Libellé du médecin d'un rendez-vous (listes, recherche, export) : son nom, ou « À attribuer —
    Cardiologie » pour un rendez-vous de département encore sans médecin."""
    if appointment.doctor is None:
        return _("À attribuer — %(department)s") % {"department": appointment.department.name}
    user = appointment.doctor.user
    return user.get_full_name() or user.get_username()


def doctor_appointments_q(doctor):
    """Rendez-vous visibles par un médecin : les siens, plus les rendez-vous de son département encore
    à attribuer (en attente ou confirmés) — liste, recherche globale et « ses patients »
    (patients/access.py)."""
    q = models.Q(doctor=doctor)
    if doctor.department_id is not None:
        q |= models.Q(doctor__isnull=True, department_id=doctor.department_id, status__in=ACTIVE_STATUSES)
    return q


def validate_no_overlap(*, clinic, doctor, patient, date, time, exclude_pk=None):
    overlapping = models.Q(patient=patient)
    if doctor is not None:
        overlapping |= models.Q(doctor=doctor)
    conflicts = Appointment.objects.filter(
        clinic=clinic, date=date, time=time, status__in=ACTIVE_STATUSES
    ).filter(overlapping)
    if exclude_pk:
        conflicts = conflicts.exclude(pk=exclude_pk)
    if conflicts.exists():
        raise ValidationError(_("Ce médecin ou ce patient a déjà un rendez-vous à cette date et à cette heure."))


def _resolve_assignment(*, clinic_id, doctor, department):
    """Médecin et/ou département d'un rendez-vous : au moins l'un des deux. Sans médecin, le
    rendez-vous attend qu'un médecin du département le prenne en charge (rotation) ; avec un médecin
    rattaché à un département, le rendez-vous porte ce département."""
    if doctor is not None and doctor.clinic_id != clinic_id:
        raise ValidationError(_("Médecin invalide."))
    if department is not None and department.clinic_id != clinic_id:
        raise ValidationError(_("Département invalide."))
    if doctor is None and department is None:
        raise ValidationError(_("Indiquez un médecin ou un département."))
    if doctor is not None and doctor.department_id is not None:
        if department is not None and department.id != doctor.department_id:
            raise ValidationError(_("Ce médecin n'appartient pas à ce département."))
        department = doctor.department
    return doctor, department


def _validate_department_open(department):
    # business/validation-rules.md VALIDATION DÉPARTEMENT : "Les rendez-vous requièrent un
    # département actif" / "Les départements inactifs ne peuvent pas recevoir de nouveaux
    # rendez-vous". Un médecin sans département assigné reste sans restriction (le département
    # est aujourd'hui optionnel sur Doctor).
    if department is not None and department.status != department.Status.ACTIVE:
        raise ValidationError(_("Ce département n'est pas actif et ne peut pas recevoir de nouveaux rendez-vous."))


@transaction.atomic
def create_appointment(*, clinic, patient, date, time, doctor=None, department=None, **fields):
    if patient.clinic_id != clinic.id:
        # Volontairement générique (audit de sécurité, 2026-09-02) : ne confirme pas si l'ID
        # soumis existe dans une autre clinique, afin d'éviter un oracle d'existence inter-tenant.
        raise ValidationError(_("Patient invalide."))
    doctor, department = _resolve_assignment(clinic_id=clinic.id, doctor=doctor, department=department)
    _validate_department_open(department)
    validate_not_past(date=date, time=time)
    validate_no_overlap(clinic=clinic, doctor=doctor, patient=patient, date=date, time=time)
    appointment = Appointment.objects.create(
        clinic=clinic, doctor=doctor, department=department, patient=patient, date=date, time=time, **fields
    )
    from .notifications import send_appointment_created_notifications

    transaction.on_commit(lambda: send_appointment_created_notifications(appointment_id=appointment.id))
    return appointment


@transaction.atomic
def update_appointment(*, appointment, **fields):
    if appointment.status in TERMINAL_STATUSES:
        raise ValidationError(_("Impossible de modifier ce rendez-vous : il est déjà %(status)s.") % {"status": _status_label(appointment)})

    date = fields.get("date", appointment.date)
    time = fields.get("time", appointment.time)
    doctor = fields.get("doctor", appointment.doctor)
    patient = fields.get("patient", appointment.patient)

    if "patient" in fields and patient.clinic_id != appointment.clinic_id:
        # Volontairement générique (audit de sécurité, 2026-09-02) : ne confirme pas si l'ID
        # soumis existe dans une autre clinique, afin d'éviter un oracle d'existence inter-tenant.
        raise ValidationError(_("Patient invalide."))
    if "doctor" in fields or "department" in fields:
        if "department" in fields:
            department = fields["department"]
        elif doctor is not None:
            department = None  # repris du médecin par _resolve_assignment
        else:
            department = appointment.department
        doctor, department = _resolve_assignment(clinic_id=appointment.clinic_id, doctor=doctor, department=department)
        if department is not None and department.id != appointment.department_id:
            _validate_department_open(department)
        fields["doctor"], fields["department"] = doctor, department

    if "date" in fields or "time" in fields:
        validate_not_past(date=date, time=time)
    if any(key in fields for key in ("date", "time", "doctor", "patient")):
        validate_no_overlap(
            clinic=appointment.clinic,
            doctor=doctor,
            patient=patient,
            date=date,
            time=time,
            exclude_pk=appointment.pk,
        )

    for key, value in fields.items():
        setattr(appointment, key, value)
    appointment.save()

    if "date" in fields or "time" in fields:
        from .notifications import send_appointment_modified_notifications

        transaction.on_commit(lambda: send_appointment_modified_notifications(appointment_id=appointment.id))

    if fields.get("status") == Appointment.Status.CANCELLED:
        from .notifications import send_appointment_cancelled_notifications

        transaction.on_commit(lambda: send_appointment_cancelled_notifications(appointment_id=appointment.id))

    return appointment


def validate_cancellable_by_patient(*, appointment):
    if appointment.status not in PATIENT_CANCELLABLE_STATUSES:
        raise ValidationError(_("Impossible d'annuler ce rendez-vous : il est déjà %(status)s.") % {"status": _status_label(appointment)})
    appointment_dt = timezone.make_aware(datetime.combine(appointment.date, appointment.time))
    deadline = timezone.now() + timedelta(hours=settings.APPOINTMENT_CANCELLATION_DEADLINE_HOURS)
    if appointment_dt <= deadline:
        raise ValidationError(
            _("Un rendez-vous ne peut être annulé que plus de %(hours)s h à l'avance.")
            % {"hours": settings.APPOINTMENT_CANCELLATION_DEADLINE_HOURS}
        )


@transaction.atomic
def cancel_appointment_by_patient(*, appointment):
    """Transition dédiée et volontairement restreinte pour l'action d'auto-annulation du patient —
    délibérément non routée via update_appointment, qui reste sans restriction de transition pour
    les appelants staff."""
    validate_cancellable_by_patient(appointment=appointment)
    appointment.status = Appointment.Status.CANCELLED
    appointment.save(update_fields=["status", "updated_at"])

    from .notifications import send_appointment_cancelled_notifications

    transaction.on_commit(lambda: send_appointment_cancelled_notifications(appointment_id=appointment.id))
    return appointment


def _lock_active(appointment):
    """Relit le rendez-vous verrouillé (deux médecins ne peuvent pas prendre le même) et vérifie
    qu'il est encore à venir."""
    appointment = (
        # of=("self",) : seul le rendez-vous est verrouillé. PostgreSQL refuse FOR UPDATE sur le côté
        # facultatif d'une jointure externe (department est nullable) — SQLite, lui, l'ignore.
        Appointment.objects.select_for_update(of=("self",))
        .select_related("clinic", "patient", "department")
        .get(pk=appointment.pk)
    )
    if appointment.status not in ACTIVE_STATUSES:
        raise ValidationError(
            _("Impossible d'attribuer ce rendez-vous : il est déjà %(status)s.") % {"status": _status_label(appointment)}
        )
    return appointment


@transaction.atomic
def take_appointment(*, appointment, doctor):
    """Un médecin du département prend en charge un rendez-vous encore à attribuer."""
    appointment = _lock_active(appointment)
    if appointment.doctor_id is not None:
        raise ValidationError(_("Ce rendez-vous a déjà été pris en charge par un médecin."))
    if doctor.department_id is None or doctor.department_id != appointment.department_id:
        raise ValidationError(_("Ce rendez-vous n'est pas destiné à votre département."))
    validate_no_overlap(
        clinic=appointment.clinic, doctor=doctor, patient=appointment.patient,
        date=appointment.date, time=appointment.time, exclude_pk=appointment.pk,
    )
    appointment.doctor = doctor
    appointment.save(update_fields=["doctor", "updated_at"])
    return appointment


@transaction.atomic
def assign_appointment(*, appointment, doctor):
    """L'accueil ou l'administrateur attribue le rendez-vous à un médecin, le réattribue, ou le
    remet à la disposition du département (`doctor=None`). Le médecin désigné est averti.
    Renvoie le rendez-vous et l'identifiant du médecin précédent (journal d'audit)."""
    appointment = _lock_active(appointment)
    previous_doctor_id = appointment.doctor_id
    if doctor is None:
        if appointment.department_id is None:
            raise ValidationError(_("Ce rendez-vous n'a pas de département : il doit garder un médecin."))
    else:
        doctor, department = _resolve_assignment(clinic_id=appointment.clinic_id, doctor=doctor, department=None)
        appointment.department = department or appointment.department
        validate_no_overlap(
            clinic=appointment.clinic, doctor=doctor, patient=appointment.patient,
            date=appointment.date, time=appointment.time, exclude_pk=appointment.pk,
        )
    appointment.doctor = doctor
    appointment.save(update_fields=["doctor", "department", "updated_at"])

    if doctor is not None and doctor.id != previous_doctor_id:
        from .notifications import notify_doctor_of_assignment

        notify_doctor_of_assignment(appointment_id=appointment.id)  # créée à la validation (notify_in_app)
    return appointment, previous_doctor_id


@transaction.atomic
def check_in_appointment(*, appointment):
    """Enregistrement à l'accueil : le patient est arrivé pour un rendez-vous du jour même. Génère
    un ticket_number par clinique/par année via le même mécanisme SequenceCounter déjà utilisé
    pour Patient.patient_number (patients/services/__init__.py::generate_patient_number) — pas un
    nouveau concept de compteur."""
    if appointment.checked_in_at is not None:
        raise ValidationError(_("L'arrivée du patient a déjà été enregistrée pour ce rendez-vous."))
    if appointment.status not in ACTIVE_STATUSES:
        raise ValidationError(
            _("Impossible d'enregistrer l'arrivée : ce rendez-vous est déjà %(status)s.") % {"status": _status_label(appointment)}
        )
    if appointment.date != timezone.localdate():
        raise ValidationError(_("L'arrivée ne peut être enregistrée que pour un rendez-vous prévu aujourd'hui."))

    year = timezone.localdate().year
    sequence = SequenceCounter.next_value(clinic=appointment.clinic, key="checkin_ticket", year=year)
    appointment.checked_in_at = timezone.now()
    appointment.ticket_number = f"CHK-{year}-{sequence:05d}"
    appointment.save(update_fields=["checked_in_at", "ticket_number", "updated_at"])

    from .notifications import notify_doctor_of_check_in

    notify_doctor_of_check_in(appointment=appointment)
    return appointment
