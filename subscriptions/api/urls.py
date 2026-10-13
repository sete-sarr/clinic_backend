from django.urls import path

from .views import (
    BillingPortalView,
    ChangePlanView,
    CheckoutSessionView,
    SasPayWebhookView,
    StripeWebhookView,
    SubscriptionOverviewView,
    SubscriptionPaymentRefreshView,
)

urlpatterns = [
    path("webhook/", StripeWebhookView.as_view(), name="subscriptions-webhook"),
    path("checkout-session/", CheckoutSessionView.as_view(), name="subscriptions-checkout-session"),
    path("billing-portal/", BillingPortalView.as_view(), name="subscriptions-billing-portal"),
    path("change-plan/", ChangePlanView.as_view(), name="subscriptions-change-plan"),
    path("saspay/webhook/", SasPayWebhookView.as_view(), name="subscriptions-saspay-webhook"),
    path("overview/", SubscriptionOverviewView.as_view(), name="subscriptions-overview"),
    path("payments/<int:pk>/refresh/", SubscriptionPaymentRefreshView.as_view(), name="subscriptions-payment-refresh"),
]
