from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter
from rest_framework.response import Response

from billing.models import Invoice
from billing.permissions import CanManageInvoices
from billing.services import cancel_invoice, issue_invoice
from billing.services.pdf import render_invoice_pdf
from common.audit import record_audit
from common.exports import CsvExportMixin
from common.models import AuditLog
from common.permissions import in_role
from common.viewsets import TenantScopedModelViewSet

from .serializers import InvoiceSerializer


class InvoiceViewSet(CsvExportMixin, TenantScopedModelViewSet):
    serializer_class = InvoiceSerializer
    permission_classes = TenantScopedModelViewSet.permission_classes + [CanManageInvoices]
    queryset = Invoice.objects.select_related("patient", "doctor__user", "clinic").prefetch_related(
        "lines", "payments"
    )
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["status", "patient"]
    search_fields = ["number", "patient__first_name", "patient__last_name", "patient__patient_number"]
    export_fields = [
        "number",
        "issue_date",
        "patient_display",
        "status",
        "subtotal",
        "vat_amount",
        "total_amount",
        "amount_paid",
        "balance_due",
    ]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if in_role(user, "patient") and not in_role(user, "doctor", "secretary", "accountant", "clinic_admin"):
            patient_profile = getattr(user, "patient_profile", None)
            return qs.filter(patient=patient_profile) if patient_profile else qs.none()
        return qs

    def perform_create(self, serializer):
        invoice = serializer.save(clinic=self.request.user.clinic)
        record_audit(user=self.request.user, action=AuditLog.Action.CREATE, obj=invoice)

    def perform_update(self, serializer):
        invoice = serializer.save()
        record_audit(user=self.request.user, action=AuditLog.Action.UPDATE, obj=invoice)

    @action(detail=True, methods=["post"], url_path="issue")
    def issue(self, request, pk=None):
        invoice = self.get_object()
        try:
            invoice = issue_invoice(invoice=invoice, actor=request.user)
        except DjangoValidationError as exc:
            return Response({"code": 400, "message": exc.messages[0], "field": None}, status=400)
        record_audit(user=request.user, action=AuditLog.Action.UPDATE, obj=invoice, metadata={"transition": "issue"})
        return Response(InvoiceSerializer(invoice).data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        if not in_role(request.user, "clinic_admin"):
            raise PermissionDenied("Only a clinic admin can cancel an invoice.")
        invoice = self.get_object()
        try:
            invoice = cancel_invoice(invoice=invoice, actor=request.user)
        except DjangoValidationError as exc:
            return Response({"code": 400, "message": exc.messages[0], "field": None}, status=400)
        record_audit(user=request.user, action=AuditLog.Action.CANCEL, obj=invoice)
        return Response(InvoiceSerializer(invoice).data)

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        invoice = self.get_object()
        try:
            pdf_bytes = render_invoice_pdf(invoice=invoice, user=request.user)
        except PermissionError:
            return Response({"code": 403, "message": "Not allowed to access this document.", "field": None}, status=403)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="invoice-{invoice.number or invoice.id}.pdf"'
        return response
