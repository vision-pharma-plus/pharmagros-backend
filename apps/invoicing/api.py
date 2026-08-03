import logging

from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import BusinessRuleViolation, ServiceUnavailable
from apps.core.permissions import HasPermission

from . import services
from .models import FiscalStatus, Invoice, InvoiceStatus, Payment, PaymentReceipt
from .serializers import (
    AllocatePaymentSerializer,
    CancelInvoiceSerializer,
    CreditNoteSerializer,
    InvoiceCreateSerializer,
    InvoiceListSerializer,
    InvoiceSerializer,
    PaymentCreateSerializer,
    PaymentReceiptSerializer,
    PaymentSerializer,
    ReversePaymentSerializer,
)

logger = logging.getLogger(__name__)


class InvoiceFilter(filters.FilterSet):
    date_from = filters.DateTimeFilter(field_name="invoice_date", lookup_expr="gte")
    date_to = filters.DateTimeFilter(field_name="invoice_date", lookup_expr="lte")
    overdue = filters.BooleanFilter(method="filter_overdue")
    unpaid = filters.BooleanFilter(method="filter_unpaid")
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = Invoice
        fields = [
            "status", "invoice_type", "customer", "is_credit_sale", "fiscal_status",
        ]

    def filter_overdue(self, queryset, name, value):
        today = timezone.localdate()
        open_states = [InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE]
        if value:
            return queryset.filter(
                status__in=open_states, due_date__lt=today, balance_due__gt=0
            )
        return queryset.exclude(
            status__in=open_states, due_date__lt=today, balance_due__gt=0
        )

    def filter_unpaid(self, queryset, name, value):
        return (
            queryset.filter(balance_due__gt=0)
            if value
            else queryset.filter(balance_due__lte=0)
        )

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(invoice_number__icontains=value)
            | Q(customer_name__icontains=value)
            | Q(customer_nif__icontains=value)
            | Q(reference__icontains=value)
        )


@extend_schema_view(
    list=extend_schema(tags=["invoicing"], summary="List invoices"),
    retrieve=extend_schema(tags=["invoicing"], summary="Retrieve an invoice"),
)
class InvoiceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Invoices.

    There is no update or destroy route. A draft is edited through the sale it
    belongs to; a posted invoice is a fiscal document that may only be
    cancelled with a reason or corrected by a credit note.
    """

    queryset = Invoice.objects.filter(deleted_at__isnull=True).select_related(
        "customer", "sale", "posted_by", "original_invoice"
    )
    filterset_class = InvoiceFilter
    search_fields = ["invoice_number", "customer_name", "customer_nif"]
    ordering_fields = ["invoice_date", "due_date", "total_amount", "balance_due"]
    ordering = ["-invoice_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "invoicing.view_invoice",
        "retrieve": "invoicing.view_invoice",
        "create": "invoicing.add_invoice",
        "post_invoice": "invoicing.post_invoice",
        "cancel": "invoicing.cancel_invoice",
        "credit_note": "invoicing.issue_credit_note",
        "pdf": "invoicing.print_invoice",
        "email": "invoicing.email_invoice",
        "payments": "invoicing.view_invoice",
        "declare": "invoicing.declare_invoice",
    }

    def get_serializer_class(self):
        if self.action == "list":
            return InvoiceListSerializer
        if self.action == "create":
            return InvoiceCreateSerializer
        return InvoiceSerializer

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("lines")
        if self.action != "list":
            queryset = queryset.prefetch_related(
                "corrections",
                # The detail serializer reads payment and payee off every
                # allocation. Without reaching through to them here, rendering
                # the payments-applied table is two queries per allocation
                # row — the list deliberately does not prefetch this, since it
                # renders a progress bar from `payment_progress` alone.
                "payment_allocations__payment__received_by",
            )
        return queryset

    def get_serializer_context(self):
        """
        Attach the sale lines behind a single invoice, keyed by product.

        Lets the line serializer report what is still returnable, which is
        what the credit note workflow needs to offer a goods return. Only
        built for single-invoice responses: doing it per row in a list would
        be a query per invoice for a field the list does not render.
        """
        context = super().get_serializer_context()
        if self.action not in {"retrieve", "credit_note"}:
            return context

        invoice = getattr(self, "_invoice_for_context", None)
        if invoice is None or invoice.sale_id is None:
            return context

        from apps.sales.models import SaleLine

        context["sale_lines_by_product"] = {
            sale_line.product_id: sale_line
            for sale_line in SaleLine.objects.filter(sale_id=invoice.sale_id)
        }
        return context

    def get_object(self):
        invoice = super().get_object()
        # Stashed so get_serializer_context can resolve the sale lines without
        # fetching the invoice a second time.
        self._invoice_for_context = invoice
        return invoice

    def create(self, request, *args, **kwargs):
        from apps.catalog.models import Medicine
        from apps.partners.models import Customer

        serializer = InvoiceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        customer = get_object_or_404(Customer, pk=data.pop("customer"))

        raw_lines = data.pop("lines")
        product_ids = {line["product"] for line in raw_lines if line.get("product")}
        products = {p.pk: p for p in Medicine.objects.filter(pk__in=product_ids)}
        lines = [
            {**line, "product": products.get(line["product"]) if line.get("product") else None}
            for line in raw_lines
        ]

        invoice = services.create_invoice(
            customer=customer, lines=lines, actor=request.user, **data
        )
        return Response(InvoiceSerializer(invoice).data, status=201)

    @extend_schema(tags=["invoicing"], summary="Post a draft invoice")
    @action(detail=True, methods=["post"], url_path="post")
    def post_invoice(self, request, pk=None):
        """Posting makes the invoice a fiscal document and locks it."""
        invoice = self.get_object()
        services.post_invoice(invoice, actor=request.user)
        return Response(InvoiceSerializer(invoice).data)

    @extend_schema(
        tags=["invoicing"], summary="Cancel an invoice", request=CancelInvoiceSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        invoice = self.get_object()
        serializer = CancelInvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.cancel_invoice(
            invoice, reason=serializer.validated_data["reason"], actor=request.user
        )
        return Response(InvoiceSerializer(invoice).data)

    @extend_schema(
        tags=["invoicing"], summary="Issue a credit note against this invoice",
        request=CreditNoteSerializer, responses={201: InvoiceSerializer},
    )
    @action(detail=True, methods=["post"], url_path="credit-note")
    def credit_note(self, request, pk=None):
        """The only lawful way to reduce an already-issued invoice."""
        from apps.catalog.models import Medicine

        invoice = self.get_object()
        serializer = CreditNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        raw_lines = data.pop("lines")
        product_ids = {line["product"] for line in raw_lines if line.get("product")}
        products = {p.pk: p for p in Medicine.objects.filter(pk__in=product_ids)}
        lines = [
            {**line, "product": products.get(line["product"]) if line.get("product") else None}
            for line in raw_lines
        ]

        note = services.issue_credit_note_for_invoice(
            invoice,
            lines=lines,
            reason=data["reason"],
            reason_code=data["reason_code"],
            actor=request.user,
        )
        return Response(
            InvoiceSerializer(note, context=self.get_serializer_context()).data, status=201
        )

    @extend_schema(
        tags=["invoicing"], summary="Download invoice PDF",
        parameters=[
            OpenApiParameter(
                "language", str,
                description="fr or en. Defaults to the customer's preference.",
            )
        ],
        responses={200: bytes},
    )
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """
        Render the invoice as PDF.

        Every render is registered so reprints remain auditable, but the page
        itself is identical on every copy — no DUPLICATE marking.
        """
        invoice = self.get_object()
        language = request.query_params.get("language") or "fr"

        from .pdf import PDFEngineUnavailable, render_invoice_pdf

        # Registered before rendering so a print is never served without a
        # corresponding audit entry.
        copy_number = services.register_print(invoice, actor=request.user)

        try:
            pdf_bytes = render_invoice_pdf(invoice, language=language)
        except PDFEngineUnavailable as exc:
            # The engine is missing (no GTK runtime on a Windows host, or the
            # package itself absent). Hand back a 503 with setup instructions
            # rather than a 500 — this is a deployment fault, not a bad
            # request, and the operator needs to know what to install.
            #
            # Nothing was produced, so give the copy number back rather than
            # leaving the counter inflated by attempts that yielded no document.
            services.release_print(
                invoice, copy_number, reason="PDF engine unavailable", actor=request.user
            )
            logger.exception("Invoice PDF rendering is unavailable")
            raise ServiceUnavailable(str(exc)) from exc

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{invoice.invoice_number}.pdf"'
        )
        return response

    @extend_schema(tags=["invoicing"], summary="Email the invoice to the customer")
    @action(detail=True, methods=["post"])
    def email(self, request, pk=None):
        invoice = self.get_object()
        if not invoice.customer.email:
            raise BusinessRuleViolation(
                "This customer has no email address on file.",
                code="customer_email_missing",
            )

        from .tasks import email_invoice

        email_invoice.delay(str(invoice.pk), request.query_params.get("language", "fr"))
        return Response({"detail": f"Invoice queued for delivery to {invoice.customer.email}."})

    @extend_schema(
        tags=["invoicing"],
        summary="Re-submit this invoice to the OBR",
        responses={200: InvoiceSerializer},
    )
    @action(detail=True, methods=["post"], url_path="declare")
    def declare(self, request, pk=None):
        """
        Declare the invoice to the OBR now, rather than waiting for the sweep.

        Intended for a document whose automatic attempts were exhausted: once
        the underlying cause is fixed, this resets it into the queue and tries
        immediately, so the operator gets an answer instead of waiting for the
        next scheduled pass.
        """
        from .fiscal import service as fiscal
        from .fiscal.client import OBRError

        invoice = self.get_object()

        if not fiscal.is_enabled():
            raise BusinessRuleViolation(
                "OBR declaration is not enabled in this environment.",
                code="obr_disabled",
            )
        if invoice.fiscal_status == FiscalStatus.DECLARED:
            raise BusinessRuleViolation(
                "This invoice has already been declared to the OBR.",
                code="already_declared",
            )
        if not fiscal.is_declarable(invoice):
            raise BusinessRuleViolation(
                "This document type is not declared to the OBR.",
                code="not_declarable",
            )

        # Clear the failure count so the retry ceiling does not immediately
        # re-reject a document a human has deliberately resubmitted.
        invoice.declaration_attempts = 0
        invoice.fiscal_status = FiscalStatus.PENDING
        invoice.save(update_fields=["declaration_attempts", "fiscal_status", "updated_at"])

        try:
            invoice = fiscal.declare_invoice(invoice, actor=request.user)
        except OBRError as exc:
            # The attempt is already recorded against the invoice and in the
            # request log; report why it failed rather than a bare 500.
            raise BusinessRuleViolation(
                f"The OBR rejected this declaration: {exc.message}",
                code="obr_rejected",
                details={"status_code": exc.status_code, "retryable": exc.retryable},
            ) from exc

        return Response(InvoiceSerializer(invoice).data)

    @extend_schema(
        tags=["invoicing"], summary="Payments applied to this invoice",
        responses={200: PaymentSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def payments(self, request, pk=None):
        invoice = self.get_object()
        payments = Payment.objects.filter(
            allocations__invoice=invoice
        ).distinct().prefetch_related("allocations")
        return Response(PaymentSerializer(payments, many=True).data)


class PaymentFilter(filters.FilterSet):
    date_from = filters.DateTimeFilter(field_name="payment_date", lookup_expr="gte")
    date_to = filters.DateTimeFilter(field_name="payment_date", lookup_expr="lte")

    class Meta:
        model = Payment
        fields = ["customer", "method", "is_reversed"]


@extend_schema_view(
    list=extend_schema(tags=["invoicing"], summary="List payments"),
    retrieve=extend_schema(tags=["invoicing"], summary="Retrieve a payment"),
)
class PaymentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Payments.

    No destroy route: a payment that arrived and was later withdrawn must
    remain visible as a reversal, which is exactly what a bounced-cheque
    investigation needs to see.
    """

    queryset = Payment.objects.filter(deleted_at__isnull=True).select_related(
        "customer", "received_by"
    ).prefetch_related("allocations__invoice")
    filterset_class = PaymentFilter
    search_fields = ["reference", "bank_reference", "customer__business_name"]
    ordering_fields = ["payment_date", "amount"]
    ordering = ["-payment_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "invoicing.view_invoice",
        "retrieve": "invoicing.view_invoice",
        "create": "invoicing.record_payment",
        "reverse": "invoicing.record_payment",
        "allocate": "invoicing.record_payment",
        "unallocated": "invoicing.view_invoice",
    }

    def get_serializer_class(self):
        return PaymentCreateSerializer if self.action == "create" else PaymentSerializer

    def create(self, request, *args, **kwargs):
        from apps.partners.models import Customer

        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        customer = get_object_or_404(Customer, pk=data.pop("customer"))
        payment = services.record_payment(customer=customer, actor=request.user, **data)
        return Response(PaymentSerializer(payment).data, status=201)

    @extend_schema(
        tags=["invoicing"],
        summary="Allocate a payment's unallocated remainder to open invoices",
        request=AllocatePaymentSerializer,
        responses={200: PaymentSerializer},
    )
    @action(detail=True, methods=["post"])
    def allocate(self, request, pk=None):
        """
        Place money already received against the customer's open invoices.

        Normally unnecessary — a remainder is drawn down automatically as soon
        as the customer's next invoice is posted. This is for directing it at
        particular invoices, or clearing a credit that has been sitting while
        no new invoice was raised.
        """
        payment = self.get_object()
        serializer = AllocatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payment = services.allocate_payment(
            payment,
            invoice_ids=serializer.validated_data.get("invoice_ids") or None,
            actor=request.user,
        )
        return Response(PaymentSerializer(payment).data)

    @extend_schema(
        tags=["invoicing"],
        summary="Payments holding unallocated money",
        responses={200: PaymentSerializer(many=True)},
    )
    @action(detail=False, methods=["get"])
    def unallocated(self, request):
        """
        Customer money that is not yet settling any invoice.

        What someone reconciling the accounts is looking for: the system holds
        this cash but has not decided which debt it clears.
        """
        payments = self.get_queryset().filter(
            is_reversed=False, allocated_amount__lt=F("amount"),
        )
        customer = request.query_params.get("customer")
        if customer:
            payments = payments.filter(customer_id=customer)
        return Response(PaymentSerializer(payments, many=True).data)

    @extend_schema(
        tags=["invoicing"], summary="Reverse a payment",
        request=ReversePaymentSerializer,
    )
    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        """Reverse (bounced cheque, erroneous entry). The record is retained."""
        payment = self.get_object()
        serializer = ReversePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.reverse_payment(
            payment, reason=serializer.validated_data["reason"], actor=request.user
        )
        return Response(PaymentSerializer(payment).data)


class PaymentReceiptFilter(filters.FilterSet):
    date_from = filters.DateTimeFilter(field_name="issued_at", lookup_expr="gte")
    date_to = filters.DateTimeFilter(field_name="issued_at", lookup_expr="lte")

    class Meta:
        model = PaymentReceipt
        fields = ["customer", "invoice"]


@extend_schema_view(
    list=extend_schema(tags=["invoicing"], summary="List payment receipts"),
    retrieve=extend_schema(tags=["invoicing"], summary="Retrieve a payment receipt"),
)
class PaymentReceiptViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Receipts acknowledging that an invoice has been settled in full.

    Read-only by design. A receipt is raised automatically when an invoice's
    balance reaches zero — never by hand, because a receipt that does not
    correspond to money actually received is a false acknowledgement. It is
    likewise voided only by the reversal that reopened its invoice.
    """

    queryset = PaymentReceipt.objects.filter(deleted_at__isnull=True).select_related(
        "invoice", "customer", "issued_by", "settling_payment"
    )
    serializer_class = PaymentReceiptSerializer
    filterset_class = PaymentReceiptFilter
    search_fields = ["receipt_number", "customer_name", "invoice__invoice_number"]
    ordering_fields = ["issued_at", "receipt_number"]
    ordering = ["-issued_at"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "invoicing.view_invoice",
        "retrieve": "invoicing.view_invoice",
        "pdf": "invoicing.view_invoice",
        "email": "invoicing.record_payment",
    }

    @extend_schema(
        tags=["invoicing"], summary="Render the payment receipt as PDF",
        parameters=[OpenApiParameter("language", str, enum=["fr", "en"])],
    )
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        receipt = self.get_object()
        language = request.query_params.get("language") or "fr"

        from .pdf import PDFEngineUnavailable, render_payment_receipt_pdf

        try:
            pdf_bytes = render_payment_receipt_pdf(receipt, language=language)
        except PDFEngineUnavailable as exc:
            # A deployment fault, not a bad request: the operator needs to know
            # what to install. Nothing was produced, so no copy is counted.
            logger.exception("Payment receipt PDF rendering is unavailable")
            raise ServiceUnavailable(str(exc)) from exc

        # Counted only once a document actually exists to hand over.
        services.register_receipt_print(receipt, actor=request.user)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{receipt.receipt_number}.pdf"'
        )
        return response

    @extend_schema(tags=["invoicing"], summary="Email the payment receipt to the customer")
    @action(detail=True, methods=["post"])
    def email(self, request, pk=None):
        receipt = self.get_object()
        if not receipt.customer.email:
            raise BusinessRuleViolation(
                "This customer has no email address on file.",
                code="customer_email_missing",
            )

        from .tasks import email_payment_receipt

        email_payment_receipt.delay(
            str(receipt.pk), request.query_params.get("language", "fr")
        )
        return Response(
            {"detail": f"Receipt queued for delivery to {receipt.customer.email}."}
        )
