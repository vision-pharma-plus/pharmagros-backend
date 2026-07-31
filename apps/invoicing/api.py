import logging

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from apps.core.exceptions import BusinessRuleViolation
from apps.core.permissions import HasPermission

from . import services
from .models import Invoice, InvoiceStatus, Payment
from .serializers import (
    CancelInvoiceSerializer,
    CreditNoteSerializer,
    InvoiceCreateSerializer,
    InvoiceListSerializer,
    InvoiceSerializer,
    PaymentCreateSerializer,
    PaymentSerializer,
    ReversePaymentSerializer,
)


logger = logging.getLogger(__name__)


class ServiceUnavailable(APIException):
    """A dependency the server needs is not installed or reachable."""

    status_code = 503
    default_code = "service_unavailable"


class InvoiceFilter(filters.FilterSet):
    date_from = filters.DateTimeFilter(field_name="invoice_date", lookup_expr="gte")
    date_to = filters.DateTimeFilter(field_name="invoice_date", lookup_expr="lte")
    overdue = filters.BooleanFilter(method="filter_overdue")
    unpaid = filters.BooleanFilter(method="filter_unpaid")
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = Invoice
        fields = ["status", "invoice_type", "customer", "is_credit_sale"]

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
    }

    def get_serializer_class(self):
        if self.action == "list":
            return InvoiceListSerializer
        if self.action == "create":
            return InvoiceCreateSerializer
        return InvoiceSerializer

    def get_queryset(self):
        return super().get_queryset().prefetch_related("lines")

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

        note = services.issue_credit_note(
            invoice, lines=lines, reason=data["reason"], actor=request.user
        )
        return Response(InvoiceSerializer(note).data, status=201)

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

        Every render is registered, and any render after the first is stamped
        DUPLICATE — an unmarked second original of a fiscal document is a
        fraud vector.
        """
        invoice = self.get_object()
        language = request.query_params.get("language") or "fr"

        is_duplicate = invoice.print_count > 0

        # Render before registering: a failed render must not inflate the
        # print count, or the next successful copy is wrongly stamped
        # DUPLICATE. The import is here because WeasyPrint needs native
        # libraries that need not be installed for the rest of the app.
        try:
            from .pdf import render_invoice_pdf

            pdf_bytes = render_invoice_pdf(
                invoice, language=language, is_duplicate=is_duplicate
            )
        except (ImportError, OSError) as exc:
            # OSError is what cffi raises when Pango/Cairo are absent — the
            # usual case on a Windows host outside Docker.
            logger.exception("Invoice PDF rendering is unavailable")
            raise ServiceUnavailable(
                "PDF rendering is unavailable on this server: the WeasyPrint "
                "native libraries (Pango/Cairo) are missing. Run the backend "
                "in Docker or install the GTK runtime."
            ) from exc

        services.register_print(invoice, actor=request.user)

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
