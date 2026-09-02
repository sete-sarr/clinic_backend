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
    """Two-factor identity check: clinic pins the row to one tenant (patient_number is only
    unique per-clinic, not globally — see docs/known-issues.md discussion), phone + date_of_birth
    confirm the requester knows this specific patient. Never used to search broadly — the OTP is
    always sent to the address already on file, never one the caller supplies."""
    return Patient.objects.filter(
        clinic_id=clinic_id,
        patient_number=patient_number,
        phone=phone,
        date_of_birth=date_of_birth,
        user__isnull=True,
        is_active=True,
    ).first()


def request_patient_activation(*, clinic_id, patient_number, phone, date_of_birth):
    """Always succeeds from the caller's point of view regardless of whether a match was found —
    enumeration protection (never reveal whether a patient_number/phone/DOB combination exists)."""
    patient = _resolve_patient(
        clinic_id=clinic_id, patient_number=patient_number, phone=phone, date_of_birth=date_of_birth
    )
    if patient is None:
        return
    try:
        generate_and_send_otp(principal=patient, purpose=OtpCode.Purpose.ACCOUNT_ACTIVATION)
    except ValidationError:
        # Cooldown already active for this patient — still a no-op from the caller's perspective.
        pass


@transaction.atomic
def activate_patient_account(*, clinic_id, patient_number, phone, date_of_birth, code, username, password):
    patient = _resolve_patient(
        clinic_id=clinic_id, patient_number=patient_number, phone=phone, date_of_birth=date_of_birth
    )
    if patient is None:
        raise ValidationError("No matching patient record was found.")

    # email is the unique login identifier platform-wide (User.USERNAME_FIELD) — Patient.email is
    # optional, so this must be checked before verify_otp burns the one-time code on an activation
    # that can never succeed.
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
    """Public tenant self-registration (business/subscription-billing-policy.md's lifecycle starts
    here: every new clinic enters on a Trial, exactly like subscriptions.services.start_trial's own
    docstring calls out as its still-missing hook point). The clinic's first user is always its
    clinic_admin — there is no other way to become clinic_admin of a brand-new tenant."""
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


# Roles a clinic_admin can assign through the staff management screen. "doctor" is deliberately
# excluded — it has its own profile model (doctors.models.Doctor) and stays owned by the doctors
# app's own create_doctor/endpoint. "patient" is excluded — created via the separate
# patients + OTP activation flow, not as staff.
STAFF_ROLES_ASSIGNABLE = ["secretary", "accountant", "clinic_admin"]


@transaction.atomic
def create_staff_member(*, clinic, role, user_data, actor):
    """Creates a User and assigns it one of STAFF_ROLES_ASSIGNABLE. Doctor creation is intentionally
    not handled here — see doctors.services.create_doctor, exposed via its own endpoint/screen."""
    user = User.objects.create_user(clinic=clinic, **user_data)
    group, _ = Group.objects.get_or_create(name=role)
    user.groups.add(group)
    record_audit(user=actor, action=AuditLog.Action.CREATE, obj=user)
    record_audit(user=actor, action=AuditLog.Action.PERMISSION_CHANGE, obj=user, metadata={"role": role})
    return user


@transaction.atomic
def update_staff_role(*, user, new_role, actor):
    """Swaps group membership among STAFF_ROLES_ASSIGNABLE only. Any transition touching "doctor"
    (in either direction) is rejected — it would require creating/deleting a Doctor profile with its
    own required fields, a materially bigger operation deliberately deferred (docs/known-issues.md)."""
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
    """business/permissions-matrix.md UTILISATEURS: "Désactiver" is allowed (unlike physical
    deletion, forbidden system-wide). Two guards enforced here, not in the view or frontend: an
    admin can never deactivate themselves, and a clinic can never be left with zero active admins."""
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
