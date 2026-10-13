import json

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.utils.translation import gettext as _
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsClinicAdmin
from subscriptions import prepaid, saspay, services
from subscriptions.catalog import ANNUAL_MONTHS_BILLED, GRACE_DAYS, TRIAL_DAYS
from subscriptions.models import SubscriptionPayment
from subscriptions.providers import get_payment_provider


class SubscriptionPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPayment
        fields = [
            "id", "plan_tier", "billing_cycle", "amount", "charged_amount", "currency", "status", "network",
            "provider_reference", "paid_at", "period_start", "period_end", "created_at",
        ]
        read_only_fields = fields


class StripeWebhookView(APIView):
    """Unauthenticated by design (Stripe calls this, not a logged-in user) — signature-verified
    via PaymentProvider.construct_webhook_event, never trusted on payload alone. First
    signature-verification endpoint in this codebase."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        result = get_payment_provider("stripe").construct_webhook_event(payload=request.body, signature=signature)
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
        if settings.SUBSCRIPTION_PAYMENT_PROVIDER == "saspay":
            # Paiement d'une période prépayée (subscriptions/prepaid.py) ; montant calculé côté serveur.
            try:
                payment = prepaid.start_prepaid_checkout(
                    clinic=clinic, plan_tier=plan_tier, billing_cycle=billing_cycle, actor=request.user
                )
            except DjangoValidationError as exc:
                return Response({"code": 400, "message": exc.messages[0], "field": None}, status=400)
            return Response({"checkout_url": payment.checkout_url, "payment_id": payment.pk})
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


class SasPayWebhookView(APIView):
    """POST /api/v1/subscriptions/saspay/webhook/ — appelé par SasPay. Sans authentification : la
    signature HMAC (X-Webhook-Signature, X-Webhook-Timestamp) est vérifiée sur le corps brut, puis
    chaque paiement est revérifié auprès de SasPay avant d'être appliqué (subscriptions/prepaid.py)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def post(self, request):
        if not saspay.webhook_signature_is_valid(
            body=request.body,
            signature=request.META.get("HTTP_X_WEBHOOK_SIGNATURE", ""),
            timestamp=request.META.get("HTTP_X_WEBHOOK_TIMESTAMP", ""),
        ):
            return Response({"detail": _("Signature invalide.")}, status=400)
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return Response({"detail": _("Signature invalide.")}, status=400)
        prepaid.handle_webhook(event=payload.get("event", ""), data=payload.get("data") or {})
        return Response({"received": True})


class SubscriptionOverviewView(APIView):
    """GET /api/v1/subscriptions/overview/ — mode de paiement, règles affichées et historique des
    paiements de la clinique (administrateur)."""

    permission_classes = [IsAuthenticated, IsClinicAdmin]

    def get(self, request):
        payments = SubscriptionPayment.objects.filter(clinic=request.user.clinic).exclude(checkout_url="")[:24]
        return Response({
            "provider": settings.SUBSCRIPTION_PAYMENT_PROVIDER,
            "trial_days": TRIAL_DAYS,
            "grace_days": GRACE_DAYS,
            "annual_months_billed": ANNUAL_MONTHS_BILLED,
            "payments": SubscriptionPaymentSerializer(payments, many=True).data,
        })


class SubscriptionPaymentRefreshView(APIView):
    """POST /api/v1/subscriptions/payments/<id>/refresh/ — au retour de la page de paiement : revérifie
    le paiement auprès de SasPay (sans attendre le webhook) et renvoie son statut."""

    permission_classes = [IsAuthenticated, IsClinicAdmin]

    def post(self, request, pk):
        payment = SubscriptionPayment.objects.filter(pk=pk, clinic=request.user.clinic).first()
        if payment is None:
            raise Http404
        payment = prepaid.refresh_payment(payment)
        return Response(SubscriptionPaymentSerializer(payment).data)
