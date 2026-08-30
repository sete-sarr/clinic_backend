from django.test import RequestFactory, TestCase

from clinics.admin import ClinicAdmin
from clinics.models import Clinic
from common.models import AuditLog
from common.testing import create_clinic, create_user
from subscriptions.models import SubscriptionEvent


class _FakeForm:
    def __init__(self, changed_data):
        self.changed_data = changed_data


class ClinicAdminSaveModelTests(TestCase):
    def setUp(self):
        self.clinic = create_clinic()
        self.admin_user = create_user(clinic=None, username="platform_admin", is_superuser=True)
        self.request = RequestFactory().post("/admin/clinics/clinic/1/change/")
        self.request.user = self.admin_user
        self.model_admin = ClinicAdmin(Clinic, admin_site=None)

    def test_status_field_change_calls_service_and_creates_audit_log(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.ACTIVE  # simulates the admin form edit
        self.model_admin.save_model(self.request, self.clinic, _FakeForm(["subscription_status"]), change=True)

        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.subscription_status, Clinic.SubscriptionStatus.ACTIVE)
        self.assertTrue(SubscriptionEvent.objects.filter(clinic=self.clinic, source="admin_manual").exists())
        self.assertTrue(
            AuditLog.objects.filter(model_name="Clinic", object_id=str(self.clinic.pk)).exists()
        )

    def test_non_status_field_change_does_not_create_subscription_event(self):
        self.clinic.phone = "0600000001"
        self.model_admin.save_model(self.request, self.clinic, _FakeForm(["phone"]), change=True)

        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.phone, "0600000001")
        self.assertFalse(SubscriptionEvent.objects.filter(clinic=self.clinic).exists())

    def test_illegal_transition_raises_validation_error(self):
        from django.core.exceptions import ValidationError

        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED  # Trial -> Suspended is illegal
        with self.assertRaises(ValidationError):
            self.model_admin.save_model(self.request, self.clinic, _FakeForm(["subscription_status"]), change=True)
