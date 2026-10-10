"""Fournisseurs d'e-mail : adresse de réponse (settings.EMAIL_REPLY_TO) jointe aux envois de la
plateforme, que l'expéditeur no-reply ne permet pas de recevoir."""

from unittest import mock

from django.core import mail
from django.test import SimpleTestCase, override_settings

from communication.providers.email_provider import DjangoEmailProvider, ResendEmailProvider


class ReplyToTests(SimpleTestCase):
    @override_settings(EMAIL_REPLY_TO="support@procli.org", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_django_provider_sets_reply_to(self):
        result = DjangoEmailProvider().send(recipient="patient@example.com", subject="Objet", body="Texte")
        self.assertTrue(result.success)
        self.assertEqual(mail.outbox[0].reply_to, ["support@procli.org"])

    @override_settings(EMAIL_REPLY_TO="support@procli.org", RESEND_API_KEY="re_test", RESEND_FROM_EMAIL="proCli <no-reply@procli.org>")
    def test_resend_payload_includes_reply_to(self):
        with mock.patch("communication.providers.email_provider.requests.post") as post:
            post.return_value.ok = True
            ResendEmailProvider().send(recipient="patient@example.com", subject="Objet", body="Texte")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["reply_to"], ["support@procli.org"])
        self.assertEqual(payload["from"], "proCli <no-reply@procli.org>")

    @override_settings(EMAIL_REPLY_TO="", RESEND_API_KEY="re_test")
    def test_no_reply_to_when_not_configured(self):
        with mock.patch("communication.providers.email_provider.requests.post") as post:
            post.return_value.ok = True
            ResendEmailProvider().send(recipient="patient@example.com", subject="Objet", body="Texte")
        self.assertNotIn("reply_to", post.call_args.kwargs["json"])
