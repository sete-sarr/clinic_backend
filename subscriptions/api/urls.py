from django.urls import path

from .views import BillingPortalView, ChangePlanView, CheckoutSessionView, StripeWebhookView

urlpatterns = [
    path("webhook/", StripeWebhookView.as_view(), name="subscriptions-webhook"),
    path("checkout-session/", CheckoutSessionView.as_view(), name="subscriptions-checkout-session"),
    path("billing-portal/", BillingPortalView.as_view(), name="subscriptions-billing-portal"),
    path("change-plan/", ChangePlanView.as_view(), name="subscriptions-change-plan"),
]
