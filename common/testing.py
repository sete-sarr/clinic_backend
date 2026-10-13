"""Shared test helpers to avoid duplicating tenant/role setup boilerplate across every app's tests."""
from django.contrib.auth.models import Group

from accounts.models import User
from clinics.models import Clinic

_counter = {"n": 0}


def _next_username(prefix):
    _counter["n"] += 1
    return f"{prefix}{_counter['n']}"


def create_clinic(name=None):
    name = name or _next_username("Clinic")
    return Clinic.objects.create(name=name, email=f"{name.lower().replace(' ', '')}@example.com")


def create_user(*, clinic, role=None, username=None, **extra):
    username = username or _next_username("user")
    extra.setdefault("email", f"{username}@example.com")
    user = User.objects.create_user(username=username, password="pass1234!", clinic=clinic, **extra)
    if role:
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
    return user


def link_doctor_to_patient(doctor, patient):
    """Donne au médecin un rendez-vous avec le patient : sans ce lien de soin, un médecin ne voit pas
    le patient et ne peut créer aucun acte pour lui (patients/access.py)."""
    from datetime import date, time, timedelta

    from appointments.models import Appointment

    return Appointment.objects.create(
        clinic=patient.clinic, patient=patient, doctor=doctor,
        date=date.today() + timedelta(days=30), time=time(8, 0), reason="Suivi",
    )
