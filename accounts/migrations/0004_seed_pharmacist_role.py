from django.db import migrations

ROLES = ["pharmacist"]


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for role in ROLES:
        Group.objects.get_or_create(name=role)


def remove_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_alter_user_options_alter_user_email_and_more")]

    operations = [migrations.RunPython(create_groups, remove_groups)]
