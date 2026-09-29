from django.conf import settings
from django.utils.translation import gettext as _
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsClinicAdmin
from subscriptions import services
from subscriptions.providers import get_payment_provider


class StripeWebhookView(APIView):
    """Unauthenticated by design (Stripe calls this, not a logged-in user) — signature-verified
    via PaymentProvider.construct_webhook_event, never trusted on payload alone. First
    signature-verification endpoint in this codebase."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        result = get_payment_provider().construct_webhook_event(payload=request.body, signature=signature)
        if not result.success:
            return Response({"detail": _("Signature invalide.")}, status=400)
        services.handle_stripe_event(event_type=result.event_type, event_id=result.event_id, payload=result.payload)
        return Response({"received": True}, status=200)


class CheckoutSessionView(APIView):
    """POST /api/v1/subscriptions/checkout-session/ — clinic_admin starts/upgrades a subscription."""

    permission_classes = [IsAuthenticated, IsClinicAdmin]

    def post(self, request):
        clinic = request.user.clinic
        plan_tier = request.data.get("plan_tier")
        billing_cycle = request.data.get("billing_cycle")
        result = services.start_checkout(
            clinic=clinic, plan_tier=plan_tier, billing_cycle=billing_cycle,
            success_url=f"{settings.FRONTEND_BASE_URL}/dashboard?checkout=success",
            cancel_url=f"{settings.FRONTEND_BASE_URL}/dashboard?checkout=cancelled",
        )
        if not result.success:
            # Format d'erreur standard {code, message, field} (docs/api-guidelines.md), lu par parseApiError.
            return Response({"code": 400, "message": result.error_message, "field": None}, status=400)
        return Response({"checkout_url": result.checkout_url})


class ChangePlanView(APIView):
    """POST /api/v1/subscriptions/change-plan/ — clinic_admin d'une clinique déjà abonnée change de
    formule/cycle ; modifie l'abonnement existant au lieu d'en créer un second."""

    permission_classes = [IsAuthenticated, IsClinicAdmin]

    def post(self, request):
        clinic = request.user.clinic
        result = services.change_subscribed_plan(
            clinic=clinic, plan_tier=request.data.get("plan_tier"),
            billing_cycle=request.data.get("billing_cycle"), actor=request.user,
        )
        if not result.success:
            return Response({"code": 400, "message": result.error_message, "field": None}, status=400)
        return Response({"plan_tier": clinic.plan_tier, "billing_cycle": clinic.billing_cycle})


class BillingPortalView(APIView):
    """POST /api/v1/subscriptions/billing-portal/ — "Manage subscription" button target."""

    permission_classes = [IsAuthenticated, IsClinicAdmin]

    def post(self, request):
        clinic = request.user.clinic
        result = services.start_billing_portal_session(
            clinic=clinic, return_url=f"{settings.FRONTEND_BASE_URL}/dashboard"
        )
        if not result.success:
            return Response({"code": 400, "message": result.error_message, "field": None}, status=400)
        return Response({"portal_url": result.portal_url})
