from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from common.audit import record_audit
from common.exports import CsvExportMixin
from common.models import AuditLog
from common.permissions import in_role
from common.viewsets import TenantScopedMixin
from payments.models import Payment
from payments.permissions import CanManagePayments
from payments.services import refund_payment
from payments.services.pdf import render_receipt_pdf

from .serializers import PaymentSerializer


class PaymentViewSet(
    CsvExportMixin,
    TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """No update, no delete — business/permissions-matrix.md: payments are immutable once validated."""

    serializer_class = PaymentSerializer
    permission_classes = TenantScopedMixin.permission_classes + [CanManagePayments]
    queryset = Payment.objects.select_related("invoice", "invoice__patient", "clinic").all()
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["status", "method", "invoice"]
    search_fields = [
        "invoice__number",
        "invoice__patient__first_name",
        "invoice__patient__last_name",
        "invoice__patient__patient_number",
    ]
    export_fields = ["date", "invoice_number", "patient_display", "method", "amount", "status"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if in_role(user, "patient") and not in_role(user, "doctor", "secretary", "accountant", "clinic_admin"):
            patient_profile = getattr(user, "patient_profile", None)
            return qs.filter(invoice__patient=patient_profile) if patient_profile else qs.none()
        return qs

    def perform_create(self, serializer):
        payment = serializer.save(clinic=self.request.user.clinic)
        record_audit(user=self.request.user, action=AuditLog.Action.CREATE, obj=payment)

    @action(detail=True, methods=["post"], url_path="refund")
    def refund(self, request, pk=None):
        if not in_role(request.user, "clinic_admin"):
            raise PermissionDenied("Only a clinic admin can approve a refund.")
        payment = self.get_object()
        try:
            payment = refund_payment(payment=payment)
        except DjangoValidationError as exc:
            return Response({"code": 400, "message": exc.messages[0], "field": None}, status=400)
        record_audit(user=request.user, action=AuditLog.Action.CANCEL, obj=payment, metadata={"transition": "refund"})
        return Response(PaymentSerializer(payment).data)

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        payment = self.get_object()
        try:
            pdf_bytes = render_receipt_pdf(payment=payment, user=request.user)
        except PermissionError:
            return Response({"code": 403, "message": "Not allowed to access this document.", "field": None}, status=403)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="receipt-{payment.id}.pdf"'
        return response
