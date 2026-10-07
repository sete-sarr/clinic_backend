"""Laboratoire (docs/laboratory.md) : machine à états, rôles, périmètre, hors norme, facturation,
notifications et isolation entre cliniques."""

from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import Invoice
from common.models import AuditLog
from common.testing import create_clinic, create_user
from communication.models import InAppNotification, NotificationLog
from doctors.models import Doctor
from laboratory.models import LabOrder, LabResult, LabTest
from laboratory.services import is_abnormal
from patients.models import Patient

PDF_BYTES = b"%PDF-1.4\n%test\n"


def _doctor(clinic):
    user = create_user(clinic=clinic, role="doctor", first_name="Awa", last_name="Diop")
    return Doctor.objects.create(user=user, clinic=clinic, professional_number=f"DOC-{user.pk}", specialty="General")


def _patient(clinic, number="PAT-2026-00001", user=None):
    return Patient.objects.create(
        clinic=clinic, patient_number=number, first_name="Grace", last_name="Hopper", phone="770000000",
        email=f"{number.lower()}@example.com", date_of_birth=date(1990, 1, 1), gender=Patient.Gender.FEMALE, user=user,
    )


class IsAbnormalTests(APITestCase):
    def test_numeric_out_of_range(self):
        self.assertTrue(is_abnormal("2,10", Decimal("0.70"), Decimal("1.10")))
        self.assertTrue(is_abnormal("0.5", Decimal("0.70"), None))
        self.assertFalse(is_abnormal("0.95", Decimal("0.70"), Decimal("1.10")))

    def test_qualitative_or_without_reference_never_flagged(self):
        self.assertFalse(is_abnormal("négatif", Decimal("0.70"), Decimal("1.10")))
        self.assertFalse(is_abnormal("12", None, None))
        self.assertFalse(is_abnormal("NaN", Decimal("0"), Decimal("1")))


class LaboratoryWorkflowTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinic A")
        self.doctor = _doctor(self.clinic)
        self.other_doctor = _doctor(self.clinic)
        self.technician = create_user(clinic=self.clinic, role="lab_technician")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")
        self.secretary = create_user(clinic=self.clinic, role="secretary")
        self.patient_user = create_user(clinic=self.clinic, role="patient")
        self.patient = _patient(self.clinic, user=self.patient_user)
        self.glucose = LabTest.objects.create(
            clinic=self.clinic, code="GLY", name="Glycémie", price=Decimal("5000"), unit="g/L",
            reference_min=Decimal("0.70"), reference_max=Decimal("1.10"),
        )
        self.crp = LabTest.objects.create(clinic=self.clinic, code="CRP", name="CRP", price=Decimal("7000"), unit="mg/L")

    # ------------------------------------------------------------- helpers

    def _as(self, user):
        self.client.force_authenticate(user)

    def _post(self, name, order_id=None, data=None, **kwargs):
        args = [order_id] if order_id is not None else []
        args += list(kwargs.values())
        return self.client.post(reverse(name, args=args), data or {}, format="json")

    def _create_order(self, tests=None):
        self._as(self.doctor.user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post("lab-order-list", data={
                "patient": self.patient.id, "tests": [t.id for t in (tests or [self.glucose, self.crp])],
                "clinical_note": "Bilan de contrôle",
            })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response.data

    def _to_in_progress(self, order_id):
        self._as(self.technician)
        self.assertEqual(self._post("lab-order-collect", order_id).status_code, 200)
        self.assertEqual(self._post("lab-order-start", order_id).status_code, 200)

    def _enter(self, order, values):
        items = {item["test_code"]: item["id"] for item in order["items"]}
        with self.captureOnCommitCallbacks(execute=True):
            return self._post("lab-order-results", order["id"], data={
                "results": [{"item": items[code], "value": value} for code, value in values.items()],
            })

    def _validated_order(self):
        order = self._create_order()
        self._to_in_progress(order["id"])
        self._enter(order, {"GLY": "0.95", "CRP": "3"})
        with self.captureOnCommitCallbacks(execute=True):
            self._post("lab-order-complete", order["id"])
        self._as(self.doctor.user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post("lab-order-validate", order["id"])
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    # ------------------------------------------------------------- création

    def test_doctor_creates_order_with_frozen_items_and_notifies_technicians(self):
        order = self._create_order()
        self.assertTrue(order["number"].startswith("LAB-"))
        self.assertEqual(order["status"], "requested")
        self.assertEqual(order["doctor"], self.doctor.id)
        self.glucose.price = Decimal("9999")
        self.glucose.save()
        item = LabOrder.objects.get(pk=order["id"]).items.get(test=self.glucose)
        self.assertEqual(item.price, Decimal("5000"))
        notification = InAppNotification.objects.get(recipient=self.technician)
        self.assertEqual(notification.priority, InAppNotification.Priority.HIGH)
        self.assertEqual(notification.link, f"/laboratory/orders/{order['id']}")

    def test_only_doctor_creates_orders(self):
        for user in (self.technician, self.secretary, self.admin):
            self._as(user)
            response = self._post("lab-order-list", data={"patient": self.patient.id, "tests": [self.glucose.id]})
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_archived_or_foreign_test_rejected(self):
        self.crp.is_active = False
        self.crp.save()
        foreign = LabTest.objects.create(clinic=create_clinic("Clinic B"), code="X", name="X", price=1)
        self._as(self.doctor.user)
        for test in (self.crp, foreign):
            response = self._post("lab-order-list", data={"patient": self.patient.id, "tests": [test.id]})
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------- workflow

    def test_full_workflow_bills_and_notifies_patient(self):
        order = self._validated_order()
        self.assertEqual(order["status"], "validated")
        invoice = Invoice.objects.get(pk=order["invoice"])
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.subtotal, Decimal("12000.00"))
        self.assertEqual(invoice.lines.count(), 2)
        self.assertTrue(
            NotificationLog.objects.filter(
                notification_type=NotificationLog.NotificationType.LAB_RESULT_AVAILABLE, channel="email",
                recipient_address=self.patient.email,
            ).exists()
        )
        body = NotificationLog.objects.get(channel="email", notification_type="lab_result_available").body
        self.assertNotIn("0.95", body)
        self.assertTrue(InAppNotification.objects.filter(recipient=self.doctor.user, title__icontains="valider").exists())

    def test_transitions_follow_state_machine(self):
        order = self._create_order()
        self._as(self.technician)
        self.assertEqual(self._post("lab-order-start", order["id"]).status_code, 400)
        self.assertEqual(self._post("lab-order-complete", order["id"]).status_code, 400)
        response = self._enter(order, {"GLY": "1"})
        self.assertEqual(response.status_code, 400)

    def test_cannot_complete_without_all_results(self):
        order = self._create_order()
        self._to_in_progress(order["id"])
        self._enter(order, {"GLY": "0.9"})
        self.assertEqual(self._post("lab-order-complete", order["id"]).status_code, 400)

    def test_pdf_attachment_counts_as_result(self):
        order = self._create_order([self.crp])
        self._to_in_progress(order["id"])
        item_id = order["items"][0]["id"]
        url = reverse("lab-order-attachment", args=[order["id"], item_id])
        rejected = self.client.post(url, {"file": SimpleUploadedFile("x.pdf", b"not a pdf")}, format="multipart")
        self.assertEqual(rejected.status_code, 400)
        accepted = self.client.post(url, {"file": SimpleUploadedFile("crp.pdf", PDF_BYTES)}, format="multipart")
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data["items"][0]["result"]["attachment_name"], "crp.pdf")
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self._post("lab-order-complete", order["id"]).status_code, 200)
        download = self.client.get(url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(bytes(download.content), PDF_BYTES)

    def test_abnormal_value_flagged_and_doctor_alerted_critical(self):
        order = self._create_order()
        self._to_in_progress(order["id"])
        response = self._enter(order, {"GLY": "2,10", "CRP": "4"})
        glucose = next(item for item in response.data["items"] if item["test_code"] == "GLY")
        self.assertTrue(glucose["result"]["is_abnormal"])
        self.assertTrue(response.data["has_abnormal"])
        alert = InAppNotification.objects.get(recipient=self.doctor.user, priority=InAppNotification.Priority.CRITICAL)
        self.assertNotIn("2,10", alert.title + alert.body)
        # Nouvelle saisie toujours hors norme : pas de seconde alerte.
        self._enter(order, {"GLY": "2,20"})
        self.assertEqual(InAppNotification.objects.filter(priority=InAppNotification.Priority.CRITICAL).count(), 1)

    def test_only_prescribing_doctor_validates(self):
        order = self._create_order()
        self._to_in_progress(order["id"])
        self._enter(order, {"GLY": "0.9", "CRP": "2"})
        with self.captureOnCommitCallbacks(execute=True):
            self._post("lab-order-complete", order["id"])
        for user in (self.other_doctor.user, self.technician, self.admin):
            self._as(user)
            self.assertIn(self._post("lab-order-validate", order["id"]).status_code, (403, 404))
        self.assertEqual(LabOrder.objects.get(pk=order["id"]).status, LabOrder.Status.COMPLETED)

    def test_validated_results_are_immutable_and_corrections_keep_history(self):
        order = self._validated_order()
        self._as(self.technician)
        self.assertEqual(self._enter(order, {"GLY": "1.5"}).status_code, 400)
        item_id = next(item["id"] for item in order["items"] if item["test_code"] == "GLY")
        no_reason = self._post("lab-order-correct", order["id"], data={"value": "1.0", "reason": ""}, item_id=item_id)
        self.assertEqual(no_reason.status_code, 400)
        response = self._post(
            "lab-order-correct", order["id"], data={"value": "1.0", "reason": "Erreur de transcription"}, item_id=item_id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        glucose = next(item for item in response.data["items"] if item["id"] == item_id)
        self.assertEqual(glucose["result"]["value"], "1.0")
        self.assertEqual(glucose["history"][0]["value"], "0.95")
        self.assertEqual(LabResult.objects.filter(item_id=item_id).count(), 2)

    def test_cancel_only_before_collection(self):
        order = self._create_order()
        self._as(self.doctor.user)
        self.assertEqual(self._post("lab-order-cancel", order["id"]).status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.CANCEL, model_name="LabOrder").exists())
        second = self._create_order()
        self._to_in_progress(second["id"])
        self._as(self.doctor.user)
        self.assertEqual(self._post("lab-order-cancel", second["id"]).status_code, 400)

    # ------------------------------------------------------------- visibilité

    def test_technician_never_sees_billing_or_consultation(self):
        order = self._validated_order()
        self._as(self.technician)
        data = self.client.get(reverse("lab-order-detail", args=[order["id"]])).data
        self.assertNotIn("invoice", data)
        self.assertNotIn("consultation", data)
        self.assertNotIn("price", data["items"][0])
        self.assertEqual(self.client.get(reverse("lab-order-pdf", args=[order["id"]])).status_code, 403)

    def test_doctor_sees_only_own_orders(self):
        order = self._create_order()
        self._as(self.other_doctor.user)
        self.assertEqual(self.client.get(reverse("lab-order-list")).data["count"], 0)
        self.assertEqual(self.client.get(reverse("lab-order-detail", args=[order["id"]])).status_code, 404)

    def test_patient_sees_only_own_validated_orders_without_clinical_note(self):
        pending = self._create_order()
        validated = self._validated_order()
        self._as(self.patient_user)
        rows = self.client.get(reverse("lab-order-list")).data["results"]
        self.assertEqual([row["id"] for row in rows], [validated["id"]])
        self.assertNotIn("clinical_note", rows[0])
        self.assertNotIn("history", rows[0]["items"][0])
        self.assertEqual(self.client.get(reverse("lab-order-detail", args=[pending["id"]])).status_code, 404)
        other_patient = _patient(self.clinic, "PAT-2026-00002", user=create_user(clinic=self.clinic, role="patient"))
        self._as(other_patient.user)
        self.assertEqual(self.client.get(reverse("lab-order-list")).data["count"], 0)

    def test_secretary_has_no_access(self):
        self._create_order()
        self._as(self.secretary)
        self.assertEqual(self.client.get(reverse("lab-order-list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("lab-test-list")).status_code, 403)

    def test_other_clinic_cannot_see_orders(self):
        order = self._create_order()
        other_clinic = create_clinic("Clinic B")
        self._as(create_user(clinic=other_clinic, role="lab_technician"))
        self.assertEqual(self.client.get(reverse("lab-order-list")).data["count"], 0)
        self.assertEqual(self.client.get(reverse("lab-order-detail", args=[order["id"]])).status_code, 404)

    def test_retrieve_and_pdf_are_audited(self):
        order = self._validated_order()
        self._as(self.doctor.user)
        self.client.get(reverse("lab-order-detail", args=[order["id"]]))
        response = self.client.get(reverse("lab-order-pdf", args=[order["id"]]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        actions = set(AuditLog.objects.filter(model_name="LabOrder", object_id=str(order["id"])).values_list("action", flat=True))
        self.assertTrue({AuditLog.Action.VIEW, AuditLog.Action.PRINT} <= actions)


class LabTestCatalogTests(APITestCase):
    def setUp(self):
        self.clinic = create_clinic("Clinic A")
        self.admin = create_user(clinic=self.clinic, role="clinic_admin")

    def test_admin_manages_catalog_with_unique_code_and_archive(self):
        self.client.force_authenticate(self.admin)
        payload = {"code": "GLY", "name": "Glycémie", "price": "5000", "unit": "g/L", "reference_min": "0.7", "reference_max": "1.1"}
        created = self.client.post(reverse("lab-test-list"), payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        duplicate = self.client.post(reverse("lab-test-list"), {**payload, "code": "gly"}, format="json")
        self.assertEqual(duplicate.status_code, 400)
        inverted = self.client.post(reverse("lab-test-list"), {**payload, "code": "X", "reference_min": "2"}, format="json")
        self.assertEqual(inverted.status_code, 400)
        archived = self.client.post(reverse("lab-test-archive", args=[created.data["id"]]))
        self.assertFalse(archived.data["is_active"])
        self.assertEqual(self.client.delete(reverse("lab-test-detail", args=[created.data["id"]])).status_code, 405)

    def test_technician_and_doctor_read_only(self):
        LabTest.objects.create(clinic=self.clinic, code="GLY", name="Glycémie", price=1)
        for role in ("lab_technician", "doctor"):
            self.client.force_authenticate(create_user(clinic=self.clinic, role=role))
            self.assertEqual(self.client.get(reverse("lab-test-list")).data["count"], 1)
            response = self.client.post(reverse("lab-test-list"), {"code": "B", "name": "B", "price": "1"}, format="json")
            self.assertEqual(response.status_code, 403)
