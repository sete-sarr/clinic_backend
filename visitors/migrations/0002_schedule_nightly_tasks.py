from django.db import migrations

# Clôture des visites oubliées juste après minuit, puis purge au-delà de 1 an (docs/visitors.md §4, §6).
TASKS = [
    ("Close open visitor log entries", "visitors.tasks.close_open_visits", "5", "0"),
    ("Purge expired visitor log entries", "visitors.tasks.purge_old_visits", "15", "3"),
]


def create_periodic_tasks(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    for name, task, minute, hour in TASKS:
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute, hour=hour, day_of_week="*", day_of_month="*", month_of_year="*", timezone="UTC"
        )
        PeriodicTask.objects.get_or_create(name=name, defaults={"crontab": schedule, "task": task})


def remove_periodic_tasks(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__in=[name for name, *_ in TASKS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("visitors", "0001_initial"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(create_periodic_tasks, remove_periodic_tasks)]
