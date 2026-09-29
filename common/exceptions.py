from django.http import Http404
from django.utils.translation import gettext as _
from rest_framework.views import exception_handler as drf_exception_handler


def api_exception_handler(exc, context):
    """Standardized {code, message, field} error shape (docs/api-guidelines.md)."""
    response = drf_exception_handler(exc, context)
    if response is None:
        return response

    data = response.data
    field = None
    if isinstance(exc, Http404) and exc.args and str(exc.args[0]).endswith("matches the given query."):
        # get_object_or_404 lève Http404("No Patient matches the given query.") : texte anglais,
        # non traduit par Django et révélant le nom du modèle — remplacé par un message générique.
        message = _("Élément introuvable.")
    elif isinstance(data, dict) and "detail" in data and len(data) == 1:
        message = data["detail"]
    elif isinstance(data, dict) and data:
        field, message = next(iter(data.items()))
        if isinstance(message, list) and message:
            message = message[0]
    elif isinstance(data, list) and data:
        message = data[0]
    else:
        message = str(data)

    response.data = {
        "code": response.status_code,
        "message": str(message),
        "field": field,
    }
    return response
