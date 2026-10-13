"""Patients d'un médecin (business/access-policy.md « Médecin : ses propres patients »).

Un médecin (sans rôle secrétaire, comptable ou administrateur) ne voit que les patients avec qui il a
un lien de soin : rendez-vous (y compris un rendez-vous de son département encore à attribuer), consultation, ordonnance, demande d'analyses, séjour dont il est le
médecin référent, ou accès d'urgence en cours. Ce périmètre s'applique aux patients, dossiers
médicaux, factures et à la recherche globale ; il s'applique aussi à la CRÉATION (rendez-vous,
consultation, ordonnance, analyses, admission) — sinon créer un acte pour un patient quelconque le
rendrait « sien ». Un patient sans rendez-vous est d'abord enregistré par l'accueil (décision produit
du 2026-10-13) ; l'accès d'urgence (« bris de glace ») couvre les urgences, motivé et journalisé.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import PermissionDenied

from common.audit import record_audit
from common.models import AuditLog
from common.permissions import in_role

EMERGENCY_ACCESS_DURATION = timedelta(hours=24)
EMERGENCY_REASON_MIN_LENGTH = 10


def restricts_to_own_patients(user):
    """Vrai pour un médecin sans autre rôle donnant accès à toute la clinique."""
    return (
        not user.is_superuser
        and in_role(user, "doctor")
        and not in_role(user, "clinic_admin", "secretary", "accountant")
    )


def doctor_patient_ids(doctor):
    """Sous-requête des identifiants des patients du médecin `doctor`."""
    from appointments.models import Appointment
    from appointments.services import doctor_appointments_q
    from consultations.models import Consultation
    from hospitalization.models import Admission
    from laboratory.models import LabOrder
    from patients.models import EmergencyAccess, Patient
    from prescriptions.models import Prescription

    linked = (
        Q(id__in=Appointment.objects.filter(doctor_appointments_q(doctor)).values("patient_id"))
        | Q(id__in=Consultation.objects.filter(doctor=doctor).values("patient_id"))
        | Q(id__in=Prescription.objects.filter(doctor=doctor).values("patient_id"))
        | Q(id__in=LabOrder.objects.filter(doctor=doctor).values("patient_id"))
        | Q(id__in=Admission.objects.filter(doctor=doctor).values("patient_id"))
        | Q(id__in=EmergencyAccess.objects.filter(doctor=doctor, expires_at__gt=timezone.now()).values("patient_id"))
    )
    return Patient.objects.filter(clinic_id=doctor.clinic_id).filter(linked).values("id")


def own_patient_ids(user):
    """None si l'utilisateur voit tous les patients de sa clinique ; sinon la sous-requête des
    identifiants de ses patients (vide pour un compte médecin sans profil)."""
    if not restricts_to_own_patients(user):
        return None
    doctor = getattr(user, "doctor_profile", None)
    if doctor is None:
        from patients.models import Patient

        return Patient.objects.none().values("id")
    return doctor_patient_ids(doctor)


def scope_to_own_patients(queryset, user, field="patient_id"):
    """Restreint `queryset` aux patients du médecin (`field` : chemin vers l'identifiant du patient)."""
    ids = own_patient_ids(user)
    return queryset if ids is None else queryset.filter(**{f"{field}__in": ids})


def ensure_patient_accessible(user, patient):
    """Refuse qu'un médecin crée un acte pour un patient qui n'est pas le sien. Un patient d'une autre
    clinique est laissé aux contrôles existants (erreur générique « Patient invalide », sans révéler
    son existence)."""
    ids = own_patient_ids(user)
    if ids is not None and patient.clinic_id == user.clinic_id and not ids.filter(id=patient.id).exists():
        raise PermissionDenied(
            _("Ce patient ne fait pas partie de vos patients : l'accueil doit d'abord lui donner un rendez-vous avec vous.")
        )


@transaction.atomic
def grant_emergency_access(*, user, patient_number, reason):
    """Accès d'urgence (« bris de glace ») d'un médecin au dossier d'un patient qui n'est pas le sien :
    24 heures, motif obligatoire, journalisé et signalé aux administrateurs de la clinique. Le patient
    est désigné par son numéro exact — jamais par une recherche, qui révélerait d'autres patients."""
    from patients.models import EmergencyAccess, Patient

    doctor = getattr(user, "doctor_profile", None)
    if doctor is None or not in_role(user, "doctor"):
        raise PermissionDenied(_("L'accès d'urgence est réservé aux médecins."))
    reason = (reason or "").strip()
    if len(reason) < EMERGENCY_REASON_MIN_LENGTH:
        raise ValidationError(
            _("Indiquez le motif de l'accès d'urgence (%(count)s caractères au moins).")
            % {"count": EMERGENCY_REASON_MIN_LENGTH}
        )
    patient = Patient.objects.filter(
        clinic_id=user.clinic_id, patient_number__iexact=(patient_number or "").strip(), is_active=True
    ).first()
    if patient is None:
        raise ValidationError(_("Aucun patient actif ne porte ce numéro dans votre clinique."))

    access = EmergencyAccess.objects.create(
        clinic_id=user.clinic_id,
        doctor=doctor,
        patient=patient,
        reason=reason,
        expires_at=timezone.now() + EMERGENCY_ACCESS_DURATION,
    )
    record_audit(
        user=user, action=AuditLog.Action.EMERGENCY_ACCESS, obj=patient,
        metadata={"reason": reason, "expires_at": access.expires_at.isoformat()},
    )

    from patients.notifications import notify_emergency_access

    notify_emergency_access(access)
    return access
