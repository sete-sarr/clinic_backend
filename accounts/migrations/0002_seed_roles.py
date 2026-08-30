from django.db import migrations

ROLES = ["doctor", "secretary", "accountant", "clinic_admin", "patient"]


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for role in ROLES:
        Group.objects.get_or_create(name=role)


def remove_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    # accounts.0001_initial already depends on auth's Group/Permission migration
    # (via the AbstractUser groups/user_permissions M2M fields), so this is enough.
    dependencies = [("accounts", "0001_initial")]

    operations = [migrations.RunPython(create_groups, remove_groups)]
