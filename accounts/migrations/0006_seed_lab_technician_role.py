from django.db import migrations

# Phase 5.1 (docs/laboratory.md) : Technicien de laboratoire.
ROLES = ["lab_technician"]


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for role in ROLES:
        Group.objects.get_or_create(name=role)


def remove_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_user_language")]

    operations = [migrations.RunPython(create_groups, remove_groups)]
