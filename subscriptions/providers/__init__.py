from django.conf import settings
from django.utils.module_loading import import_string

_PROVIDER_PATHS = {
    "stripe": "subscriptions.providers.stripe_provider.StripePaymentProvider",
}


def get_payment_provider(name=None):
    """Fournisseur d'abonnement à prélèvement automatique (Stripe). `name` par défaut :
    SUBSCRIPTION_PAYMENT_PROVIDER — mais le paiement prépayé SasPay ne passe pas par cette
    abstraction (subscriptions/prepaid.py), d'où le repli sur Stripe pour les appels propres à Stripe
    (webhook, portail, changement de formule d'un abonnement existant)."""
    name = name or settings.SUBSCRIPTION_PAYMENT_PROVIDER
    provider_class = import_string(_PROVIDER_PATHS.get(name, _PROVIDER_PATHS["stripe"]))
    return provider_class()
