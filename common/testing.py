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
