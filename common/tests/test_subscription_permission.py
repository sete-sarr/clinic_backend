from datetime import date
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from clinics.models import Clinic
from common.testing import create_clinic, create_user
from subscriptions.providers.base import BillingPortalResult, CheckoutSessionResult


def _valid_patient_payload(suffix="1"):
    return {
        "first_name": "Suspend",
        "last_name": f"Test{suffix}",
        "phone": f"060000000{suffix}",
        "date_of_birth": date(1990, 1, 1).isoformat(),
        "gender": "male",
    }


class SubscriptionActivePermissionOnPatientsTests(APITestCase):
    """Representative additive-pattern ViewSet (TenantScopedModelViewSet.permission_classes +
    [CanManagePatients]) — docs/known-issues.md #9."""

    def setUp(self):
        self.clinic = create_clinic()
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.client.force_authenticate(self.secretary)

    def test_active_clinic_can_create_patient(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.ACTIVE
        self.clinic.save(update_fields=["subscription_status"])
        response = self.client.post(reverse("patient-list"), _valid_patient_payload("1"))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_trial_clinic_can_create_patient(self):
        response = self.client.post(reverse("patient-list"), _valid_patient_payload("2"))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_past_due_clinic_can_still_create_patient(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.PAST_DUE
        self.clinic.save(update_fields=["subscription_status"])
        response = self.client.post(reverse("patient-list"), _valid_patient_payload("3"))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_suspended_clinic_cannot_create_patient(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic.save(update_fields=["subscription_status"])
        response = self.client.post(reverse("patient-list"), _valid_patient_payload("4"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cancelled_clinic_cannot_create_patient(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.CANCELLED
        self.clinic.save(update_fields=["subscription_status"])
        response = self.client.post(reverse("patient-list"), _valid_patient_payload("5"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_suspended_clinic_can_still_read(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic.save(update_fields=["subscription_status"])
        response = self.client.get(reverse("patient-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superuser_bypasses_suspension_gate(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic.save(update_fields=["subscription_status"])
        superuser = create_user(clinic=self.clinic, username="platform_admin", is_superuser=True)
        self.client.force_authenticate(superuser)
        response = self.client.post(reverse("patient-list"), _valid_patient_payload("6"))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class SubscriptionActivePermissionIsolationTests(APITestCase):
    """docs/subscription-billing.md §4: the suspension gate must never replace or weaken
    IsSameClinic — cross-tenant isolation stays intact regardless of subscription status."""

    def setUp(self):
        self.clinic_a = create_clinic("Clinic A")
        self.clinic_b = create_clinic("Clinic B")
        self.secretary_b = create_user(clinic=self.clinic_b, role="secretary")

    def test_clinic_b_still_cannot_see_clinic_a_patients_when_clinic_a_suspended(self):
        self.clinic_a.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic_a.save(update_fields=["subscription_status"])
        from patients.services import create_patient

        patient_a = create_patient(
            clinic=self.clinic_a, first_name="A", last_name="Patient",
            phone="0611111111", date_of_birth=date(1990, 1, 1), gender="male",
        )
        self.client.force_authenticate(self.secretary_b)
        response = self.client.get(reverse("patient-detail", args=[patient_a.id]))
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))


class SubscriptionActivePermissionOnStaffTests(APITestCase):
    """StaffViewSet hardcodes its permission_classes list rather than extending
    TenantScopedMixin's — must be gated explicitly (docs/known-issues.md #9)."""

    def setUp(self):
        self.clinic = create_clinic()
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.client.force_authenticate(self.admin)

    def _staff_payload(self):
        return {
            "username": "newsecretary",
            "email": "newsecretary@example.com",
            "first_name": "New",
            "last_name": "Secretary",
            "password": "S3cure-Pass!",
            "role": "secretary",
        }

    def test_suspended_clinic_cannot_create_staff(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic.save(update_fields=["subscription_status"])
        response = self.client.post(reverse("staff-list"), self._staff_payload())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_active_clinic_can_create_staff(self):
        response = self.client.post(reverse("staff-list"), self._staff_payload())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_suspended_clinic_can_still_list_staff(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic.save(update_fields=["subscription_status"])
        response = self.client.get(reverse("staff-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SubscriptionActivePermissionOnMedicalRecordsTests(APITestCase):
    """MedicalRecordViewSet does not inherit TenantScopedMixin at all — must be gated
    explicitly (docs/known-issues.md #9)."""

    def setUp(self):
        from patients.services import create_patient

        self.clinic = create_clinic()
        self.doctor_user = create_user(clinic=self.clinic, role="doctor")
        self.patient = create_patient(
            clinic=self.clinic, first_name="Rec", last_name="Ord",
            phone="0622222222", date_of_birth=date(1990, 1, 1), gender="male",
        )
        self.record = self.patient.medical_record
        self.client.force_authenticate(self.doctor_user)

    def test_suspended_clinic_cannot_update_medical_record(self):
        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic.save(update_fields=["subscription_status"])
        response = self.client.patch(
            reverse("medical-record-detail", args=[self.record.id]), {"observations": "note"}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_active_clinic_can_update_medical_record(self):
        response = self.client.patch(
            reverse("medical-record-detail", args=[self.record.id]), {"observations": "note"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SubscriptionActivePermissionExemptsBillingEndpointsTests(APITestCase):
    """A suspended clinic must still be able to reactivate — checkout/billing-portal are plain
    APIViews outside TenantScopedMixin, unaffected by the gate by construction. These tests pin
    that behavior so it can't regress if these views are ever refactored onto the shared mixin."""

    def setUp(self):
        self.clinic = create_clinic()
        self.clinic.subscription_status = Clinic.SubscriptionStatus.SUSPENDED
        self.clinic.save(update_fields=["subscription_status"])
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.client.force_authenticate(self.admin)

    @patch("subscriptions.providers.get_payment_provider")
    def test_suspended_clinic_can_start_checkout(self, mock_get_provider):
        mock_get_provider.return_value.create_checkout_session.return_value = CheckoutSessionResult(
            success=True, provider_name="stripe", checkout_url="https://stripe.test/checkout"
        )
        response = self.client.post(
            reverse("subscriptions-checkout-session"),
            {"plan_tier": "starter", "billing_cycle": "monthly"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch("subscriptions.providers.get_payment_provider")
    def test_suspended_clinic_can_open_billing_portal(self, mock_get_provider):
        mock_get_provider.return_value.create_billing_portal_session.return_value = BillingPortalResult(
            success=True, provider_name="stripe", portal_url="https://stripe.test/portal"
        )
        response = self.client.post(reverse("subscriptions-billing-portal"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
