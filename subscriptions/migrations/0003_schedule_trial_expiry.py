from django.db import migrations

TASK_NAME = "Suspend clinics whose free trial has ended"


def create_periodic_task(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="6", day_of_week="*", day_of_month="*", month_of_year="*", timezone="UTC"
    )
    PeriodicTask.objects.get_or_create(
        name=TASK_NAME,
        defaults={"crontab": schedule, "task": "subscriptions.tasks.expire_ended_trials"},
    )


def remove_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0002_schedule_expiring_check"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(create_periodic_task, remove_periodic_task)]
