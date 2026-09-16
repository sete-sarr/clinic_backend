"""
Test de fumée en réseau réel contre la véritable API Stripe en mode test (aucun mock des appels
stripe.*). Ignoré automatiquement quand STRIPE_API_KEY n'est pas configurée, afin de ne jamais
casser la CI ou les autres machines qui n'ont pas de clé de test dans leur .env -- ceci est une
exception délibérée et restreinte à la règle "toujours mocker les services externes", dans le but
de vérifier que l'intégration réelle fonctionne effectivement de bout en bout, pas seulement que
notre code appelle correctement le SDK. Concernant son exécution dans le run par défaut de
`manage.py test` : elle en fait bien partie (pas de tag séparé), mais s'auto-ignore et dépend du
réseau, donc on la garde dans son propre fichier pour pouvoir l'exclure facilement (`test
--exclude-tag` n'est pas utilisé dans tout le dépôt, donc l'exclusion se fait aujourd'hui par
chemin de test : `manage.py test subscriptions --exclude=test_stripe_live_smoke` n'existe pas non
plus dans Django standard -- à exécuter explicitement par chemin pointé si besoin).
"""
import unittest

from django.conf import settings
from django.test import TestCase

from clinics.models import Clinic
from common.testing import create_clinic
from subscriptions.providers.stripe_provider import StripePaymentProvider

_HAS_REAL_STRIPE_KEY = bool(settings.STRIPE_API_KEY) and settings.STRIPE_API_KEY.startswith(("sk_test_", "sk_live_"))


@unittest.skipUnless(_HAS_REAL_STRIPE_KEY, "No real STRIPE_API_KEY configured in this environment.")
class StripeLiveSmokeTests(TestCase):
    """Exerce la véritable API Stripe en mode test, sur le réseau. Crée un Product/Price jetable
    directement via le SDK (pas via notre catalogue, qui lit les Price ID depuis des variables
    d'environnement qu'on ne veut pas exiger ici), puis le fait passer par notre véritable
    StripePaymentProvider.create_checkout_session -- exactement le chemin de code que
    CheckoutSessionView appelle en production, sans aucun mock sous la frontière du fournisseur."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import stripe

        stripe.api_key = settings.STRIPE_API_KEY
        cls._stripe = stripe
        cls._product = stripe.Product.create(name="[test-suite] Clinic Audit Smoke Test")
        cls._price = stripe.Price.create(
            product=cls._product.id, unit_amount=2900, currency="usd", recurring={"interval": "month"}
        )

    @classmethod
    def tearDownClass(cls):
        try:
            cls._stripe.Product.modify(cls._product.id, active=False)
        except Exception:
            pass
        super().tearDownClass()

    def setUp(self):
        self.clinic = create_clinic()
        self.provider = StripePaymentProvider()

    def test_real_checkout_session_creation(self):
        import subscriptions.providers.stripe_provider as stripe_provider_module

        original_create = self._stripe.checkout.Session.create
        created_sessions = []

        def _capturing_create(*args, **kwargs):
            session = original_create(*args, **kwargs)
            created_sessions.append(session)
            return session

        original_price_fn = stripe_provider_module.get_stripe_price_id
        stripe_provider_module.get_stripe_price_id = lambda *, plan_tier, billing_cycle: self._price.id
        stripe_provider_module.stripe.checkout.Session.create = _capturing_create
        try:
            result = self.provider.create_checkout_session(
                clinic=self.clinic,
                plan_tier=Clinic.PlanTier.STARTER,
                billing_cycle=Clinic.BillingCycle.MONTHLY,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )
        finally:
            stripe_provider_module.get_stripe_price_id = original_price_fn
            stripe_provider_module.stripe.checkout.Session.create = original_create

        self.assertTrue(result.success, f"Real Stripe checkout session creation failed: {result.error_message}")
        self.assertTrue(result.checkout_url.startswith("https://checkout.stripe.com/"))

        # Vérifie que la session réellement créée par Stripe porte les bons identifiants pour que
        # notre gestionnaire de webhook puisse ensuite résoudre la clinique
        # (subscriptions/services.py::_resolve_clinic).
        self.assertEqual(len(created_sessions), 1)
        session = created_sessions[0]
        self.assertEqual(session.client_reference_id, str(self.clinic.pk))
        self.assertEqual(session.metadata["clinic_id"], str(self.clinic.pk))
        self.assertEqual(session.mode, "subscription")

    def test_real_billing_portal_requires_customer_first(self):
        # Pas encore de stripe_customer_id -> le fournisseur doit échouer proprement sans jamais
        # appeler Stripe.
        result = self.provider.create_billing_portal_session(clinic=self.clinic, return_url="https://example.com")
        self.assertFalse(result.success)

    def test_real_billing_portal_session_with_real_customer(self):
        customer = self._stripe.Customer.create(email=self.clinic.email or "audit@example.com")
        self.clinic.stripe_customer_id = customer.id
        self.clinic.save(update_fields=["stripe_customer_id"])
        try:
            result = self.provider.create_billing_portal_session(
                clinic=self.clinic, return_url="https://example.com"
            )
            # Un compte de test Stripe tout juste créé n'a généralement pas de configuration par
            # défaut du Billing Portal, donc l'échec est légitime avec un message d'erreur propre
            # au niveau du fournisseur dans ce cas -- les deux issues sont valides ; ce qui compte,
            # c'est que rien ne remonte au-delà de la frontière du fournisseur.
            if not result.success:
                self.assertTrue(result.error_message)
            else:
                self.assertTrue(result.portal_url.startswith("https://billing.stripe.com/"))
        finally:
            self._stripe.Customer.delete(customer.id)
