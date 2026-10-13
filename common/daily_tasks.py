"""Tâches quotidiennes de la plateforme (docs/scheduled-tasks.md).

Sans worker Celery en production, elles sont déclenchées chaque matin par GitHub Actions
(.github/workflows/daily-tasks.yml) via POST /api/v1/internal/daily-tasks/. Chaque tâche est
idempotente : un second appel le même jour ne renvoie aucun message en double et ne suspend
personne deux fois. Les mêmes fonctions restent planifiées dans django-celery-beat pour le jour où un
worker tournera.
"""

import logging

logger = logging.getLogger(__name__)


def _jobs():
    from appointments.tasks import send_day_before_reminders
    from communication.tasks import purge_archived_in_app_notifications, retry_failed_notifications
    from subscriptions.prepaid import expire_prepaid_periods, refresh_pending_payments
    from subscriptions.tasks import expire_ended_trials, send_subscription_expiring_notifications
    from visitors.tasks import close_open_visits, purge_old_visits

    # Ordre : rattrapage des paiements d'abord (un paiement réglé évite une suspension), puis fin des
    # essais et des périodes prépayées, les rappels, enfin la nouvelle tentative des messages en échec
    # et les purges.
    return [
        ("refresh_pending_payments", refresh_pending_payments),
        ("expire_ended_trials", expire_ended_trials),
        ("expire_prepaid_periods", expire_prepaid_periods),
        ("send_subscription_expiring_notifications", send_subscription_expiring_notifications),
        ("send_day_before_reminders", send_day_before_reminders),
        ("close_open_visits", close_open_visits),
        ("retry_failed_notifications", retry_failed_notifications),
        ("purge_archived_in_app_notifications", purge_archived_in_app_notifications),
        ("purge_old_visits", purge_old_visits),
    ]


def run_daily_tasks():
    """Exécute chaque tâche ; l'échec de l'une n'empêche pas les suivantes. Renvoie
    {nom: {"ok": bool, "result": ...}}."""
    report = {}
    for name, job in _jobs():
        try:
            result = job()
            report[name] = {"ok": True, "result": result if isinstance(result, (int, str, type(None))) else str(result)}
        except Exception as exc:  # une tâche en échec est signalée sans bloquer les autres
            logger.exception("Tâche quotidienne %s en échec", name)
            report[name] = {"ok": False, "result": type(exc).__name__}
    return report
