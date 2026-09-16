from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from common.models import AuditLog
from common.testing import create_clinic, create_user


class LoginLogoutAuditTests(APITestCase):
    """business/access-policy.md "AUDIT POLICY" : la connexion et la déconnexion sont des événements journalisés."""

    def setUp(self):
        cache.clear()  # la connexion est désormais soumise à ScopedRateThrottle (audit de sécurité, 2026-09-02).
        self.clinic = create_clinic()
        self.user = create_user(clinic=self.clinic, role="secretary", username="secretary1")

    def test_login_records_audit_log(self):
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "secretary1@example.com", "password": "pass1234!"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(AuditLog.objects.filter(user=self.user, action=AuditLog.Action.LOGIN).exists())

    def test_logout_blacklists_refresh_token_and_records_audit(self):
        refresh = RefreshToken.for_user(self.user)
        self.client.force_authenticate(self.user)

        response = self.client.post(reverse("logout"), {"refresh": str(refresh)})
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(AuditLog.objects.filter(user=self.user, action=AuditLog.Action.LOGOUT).exists())

        # Le refresh token blacklisté ne peut plus être utilisé pour obtenir un nouveau access token.
        refresh_response = self.client.post(reverse("token_refresh"), {"refresh": str(refresh)})
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_without_refresh_token_still_succeeds(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(reverse("logout"), {})
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
