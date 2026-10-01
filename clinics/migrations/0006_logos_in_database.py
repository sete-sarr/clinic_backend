# Logos de clinique : fichiers sur disque -> table ClinicLogo (clinics/services.py).
# Ordre : création de la table, reprise des fichiers encore présents, suppression des anciens champs.

import io

import django.db.models.deletion
from django.db import migrations, models

FIELD_TO_KIND = {"logo_light": "light", "logo_dark": "dark", "logo_print": "print", "favicon": "favicon"}
CONTENT_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg"}


def copy_logo_files_to_database(apps, schema_editor):
    """Recopie les logos dont le fichier existe encore. Un fichier absent (cas de l'hébergeur dont
    le disque est effacé à chaque redéploiement) est ignoré : le logo sera simplement à
    réimporter."""
    from PIL import Image

    Clinic = apps.get_model("clinics", "Clinic")
    ClinicLogo = apps.get_model("clinics", "ClinicLogo")
    for clinic in Clinic.objects.all():
        for field, kind in FIELD_TO_KIND.items():
            file = getattr(clinic, field)
            if not file or not file.name:
                continue
            try:
                with file.open("rb") as handle:
                    content = handle.read()
                image_format = Image.open(io.BytesIO(content)).format
            except (OSError, ValueError):
                continue
            if image_format in CONTENT_TYPES:
                ClinicLogo.objects.create(
                    clinic=clinic, kind=kind, content=content, content_type=CONTENT_TYPES[image_format]
                )


class Migration(migrations.Migration):

    dependencies = [
        ('clinics', '0005_currency'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClinicLogo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('light', 'Light'), ('dark', 'Dark'), ('print', 'Print'), ('favicon', 'Favicon')], max_length=16)),
                ('content', models.BinaryField()),
                ('content_type', models.CharField(max_length=32)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('clinic', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logos', to='clinics.clinic')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('clinic', 'kind'), name='unique_logo_kind_per_clinic')],
            },
        ),
        migrations.RunPython(copy_logo_files_to_database, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='clinic',
            name='favicon',
        ),
        migrations.RemoveField(
            model_name='clinic',
            name='logo_dark',
        ),
        migrations.RemoveField(
            model_name='clinic',
            name='logo_light',
        ),
        migrations.RemoveField(
            model_name='clinic',
            name='logo_print',
        ),
    ]
