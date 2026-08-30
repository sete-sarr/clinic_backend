from rest_framework.views import exception_handler as drf_exception_handler


def api_exception_handler(exc, context):
    """Standardized {code, message, field} error shape (docs/api-guidelines.md)."""
    response = drf_exception_handler(exc, context)
    if response is None:
        return response

    data = response.data
    field = None
    if isinstance(data, dict) and "detail" in data and len(data) == 1:
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
