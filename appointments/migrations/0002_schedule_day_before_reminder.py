from django.db import migrations


def create_periodic_task(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="8", day_of_week="*", day_of_month="*", month_of_year="*", timezone="UTC"
    )
    PeriodicTask.objects.get_or_create(
        name="Send day-before appointment reminders",
        defaults={
            "crontab": schedule,
            "task": "appointments.tasks.send_day_before_reminders",
        },
    )


def remove_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="Send day-before appointment reminders").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("appointments", "0001_initial"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(create_periodic_task, remove_periodic_task)]
