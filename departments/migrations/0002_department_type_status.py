from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('departments', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='department',
            name='department_type',
            field=models.CharField(
                choices=[
                    ('medical', 'Médical'),
                    ('administrative', 'Administratif'),
                    ('technical', 'Technique'),
                    ('support', 'Support'),
                ],
                default='medical',
                max_length=20,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='department',
            name='status',
            field=models.CharField(
                choices=[
                    ('active', 'Actif'),
                    ('inactive', 'Inactif'),
                    ('archived', 'Archivé'),
                ],
                default='active',
                max_length=10,
            ),
        ),
    ]
