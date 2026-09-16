from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction

from clinics.models import Clinic
from common.audit import record_audit
from common.models import AuditLog
from communication.models import OtpCode
from communication.services import generate_and_send_otp, verify_otp
from patients.models import Patient
from subscriptions.services import start_trial

from .models import User


def _resolve_patient(*, clinic_id, patient_number, phone, date_of_birth):
    """Vérification d'identité à deux facteurs : clinic ancre l'enregistrement à un seul tenant
    (patient_number n'est unique que par clinique, pas globalement — voir la discussion dans
    docs/known-issues.md), phone + date_of_birth confirment que le demandeur connaît ce patient
    précis. Jamais utilisé pour une recherche large — l'OTP est toujours envoyé à l'adresse déjà
    enregistrée, jamais à une adresse fournie par l'appelant."""
    return Patient.objects.filter(
        clinic_id=clinic_id,
        patient_number=patient_number,
        phone=phone,
        date_of_birth=date_of_birth,
        user__isnull=True,
        is_active=True,
    ).first()


def request_patient_activation(*, clinic_id, patient_number, phone, date_of_birth):
    """Réussit toujours du point de vue de l'appelant, qu'une correspondance ait été trouvée ou
    non — protection contre l'énumération (ne jamais révéler si une combinaison
    patient_number/phone/date de naissance existe)."""
    patient = _resolve_patient(
        clinic_id=clinic_id, patient_number=patient_number, phone=phone, date_of_birth=date_of_birth
    )
    if patient is None:
        return
    try:
        generate_and_send_otp(principal=patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
    except ValidationError:
        # Cooldown déjà actif pour ce patient — reste sans effet du point de vue de l'appelant.
        pass


@transaction.atomic
def activate_patient_account(*, clinic_id, patient_number, phone, date_of_birth, code, username, password):
    patient = _resolve_patient(
        clinic_id=clinic_id, patient_number=patient_number, phone=phone, date_of_birth=date_of_birth
    )
    if patient is None:
        raise ValidationError("No matching patient record was found.")

    # email est l'identifiant de connexion unique à l'échelle de la plateforme
    # (User.USERNAME_FIELD) — Patient.email étant optionnel, cette vérification doit avoir lieu
    # avant que verify_otp ne consomme le code à usage unique pour une activation qui ne pourra
    # jamais aboutir.
    if not patient.email:
        raise ValidationError(
            "No email address is on file for this patient. Please contact your clinic to add one "
            "before activating your portal account."
        )
    if User.objects.filter(email=patient.email).exists():
        raise ValidationError("A user with that email already exists.")

    verify_otp(principal=patient, code=code, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)

    user = User.objects.create_user(
        username=username,
        password=password,
        email=patient.email,
        first_name=patient.first_name,
        last_name=patient.last_name,
        clinic_id=clinic_id,
    )
    patient_group, _ = Group.objects.get_or_create(name="patient")
    user.groups.add(patient_group)
    patient.user = user
    patient.save(update_fields=["user"])
    return user


@transaction.atomic
def register_clinic(
    *, clinic_name, clinic_email, clinic_phone, username, password, email, first_name, last_name
):
    """Auto-inscription publique d'un tenant (le cycle de vie de business/subscription-billing-policy.md
    commence ici : chaque nouvelle clinique démarre en Trial, exactement le point d'ancrage encore
    manquant que mentionne le docstring de subscriptions.services.start_trial). Le premier
    utilisateur de la clinique est toujours son clinic_admin — il n'existe aucun autre moyen de
    devenir clinic_admin d'un tenant tout juste créé."""
    clinic = Clinic.objects.create(name=clinic_name, email=clinic_email, phone=clinic_phone)
    start_trial(clinic=clinic)

    user = User.objects.create_user(
        username=username,
        password=password,
        email=email,
        first_name=first_name,
        last_name=last_name,
        clinic=clinic,
    )
    admin_group, _ = Group.objects.get_or_create(name="clinic_admin")
    user.groups.add(admin_group)

    record_audit(user=user, action=AuditLog.Action.CREATE, obj=clinic)
    record_audit(user=user, action=AuditLog.Action.CREATE, obj=user)
    return user


# Rôles qu'un clinic_admin peut attribuer via l'écran de gestion du personnel. "doctor" est
# volontairement exclu — il possède son propre modèle de profil (doctors.models.Doctor) et reste
# géré par le endpoint/create_doctor propre à l'app doctors. "patient" est exclu — créé via le
# flux séparé patients + activation OTP, pas en tant que membre du personnel.
STAFF_ROLES_ASSIGNABLE = ["secretary", "accountant", "clinic_admin", "pharmacist"]


@transaction.atomic
def create_staff_member(*, clinic, role, user_data, actor):
    """Crée un User et lui attribue l'un des STAFF_ROLES_ASSIGNABLE. La création d'un médecin n'est
    volontairement pas gérée ici — voir doctors.services.create_doctor, exposée via son propre
    endpoint/écran."""
    user = User.objects.create_user(clinic=clinic, **user_data)
    group, _ = Group.objects.get_or_create(name=role)
    user.groups.add(group)
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=user)
    record_audit(user=actor, action=AuditLog.Action.PERMISSION_CHANGE, obj=user, metadata={"role": role})
    return user


@transaction.atomic
def update_staff_role(*, user, new_role, actor):
    """Échange l'appartenance aux groupes uniquement parmi STAFF_ROLES_ASSIGNABLE. Toute transition
    touchant "doctor" (dans un sens ou dans l'autre) est rejetée — cela nécessiterait de
    créer/supprimer un profil Doctor avec ses propres champs obligatoires, une opération
    nettement plus lourde et volontairement différée (docs/known-issues.md)."""
    if new_role not in STAFF_ROLES_ASSIGNABLE:
        raise ValidationError("Unsupported role transition.")
    if user.groups.filter(name="doctor").exists():
        raise ValidationError("Cannot change a doctor's role from this screen.")
    old_roles = list(user.groups.values_list("name", flat=True))
    user.groups.clear()
    group, _ = Group.objects.get_or_create(name=new_role)
    user.groups.add(group)
    record_audit(
        user=actor,
        action=AuditLog.Action.PERMISSION_CHANGE,
        obj=user,
        metadata={"old_roles": old_roles, "new_role": new_role},
    )
    return user


@transaction.atomic
def deactivate_staff_member(*, user, actor):
    """business/permissions-matrix.md UTILISATEURS : "Désactiver" est autorisé (contrairement à la
    suppression physique, interdite dans tout le système). Deux garde-fous appliqués ici, pas dans
    la vue ni le frontend : un admin ne peut jamais se désactiver lui-même, et une clinique ne peut
    jamais se retrouver sans aucun admin actif."""
    if user.pk == actor.pk:
        raise ValidationError("You cannot deactivate your own account.")
    if user.groups.filter(name="clinic_admin").exists():
        remaining_admins = (
            User.objects.filter(clinic=user.clinic, groups__name="clinic_admin", is_active=True)
            .exclude(pk=user.pk)
            .count()
        )
        if remaining_admins == 0:
            raise ValidationError("Cannot deactivate the last active clinic administrator.")
    user.is_active = False
    user.save(update_fields=["is_active"])
    record_audit(user=actor, action=AuditLog.Action.ARCHIVE, obj=user, metadata={"reason": "deactivated"})
    return user


@transaction.atomic
def reactivate_staff_member(*, user, actor):
    user.is_active = True
    user.save(update_fields=["is_active"])
    record_audit(user=actor, action=AuditLog.Action.UPDATE, obj=user, metadata={"reason": "reactivated"})
    return user
