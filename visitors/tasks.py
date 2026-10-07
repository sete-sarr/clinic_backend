from celery import shared_task

from .services import auto_close_open_visits, purge_expired_visits


@shared_task
def close_open_visits():
    """Chaque nuit : clôture des visites restées ouvertes la veille (« sortie non enregistrée »)."""
    return auto_close_open_visits()


@shared_task
def purge_old_visits():
    """Chaque nuit : suppression des visites au-delà de la durée de conservation (1 an)."""
    return purge_expired_visits()
