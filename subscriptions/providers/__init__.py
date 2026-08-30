from django.conf import settings
from django.utils.module_loading import import_string

_PROVIDER_PATHS = {
    "stripe": "subscriptions.providers.stripe_provider.StripePaymentProvider",
}


def get_payment_provider():
    """Resolved by settings (docs/subscription-billing.md §5's explicit instruction — a stricter,
    deliberate divergence from communication/tasks.py's hardcoded _PROVIDERS dict precedent).
    Swapping gateways later is a one-line settings change, no call-site changes."""
    provider_class = import_string(_PROVIDER_PATHS[settings.SUBSCRIPTION_PAYMENT_PROVIDER])
    return provider_class()
