from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.core.fields import MoneySerializerField

from .models import (
    CreditNoteReason,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentReceipt,
)


class InvoiceLineSerializer(serializers.ModelSerializer):
    unit_price = MoneySerializerField(read_only=True)
    discount_amount = MoneySerializerField(read_only=True)
    tax_amount = MoneySerializerField(read_only=True)
    line_subtotal = MoneySerializerField(read_only=True)
    line_total = MoneySerializerField(read_only=True)

    # The originating sale line, and how much of it is still returnable.
    # A credit note for returned goods has to restock the batch the units
    # actually came from, and only the sale line knows that. Resolved in the
    # view rather than stored, because an invoice line has no FK to a sale
    # line — the two are matched on product within the invoice's own sale.
    sale_line = serializers.SerializerMethodField()
    returnable_quantity = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceLine
        fields = [
            "id", "line_number", "product", "product_code", "description",
            "batch_numbers", "expiry_dates",
            "quantity", "unit_of_measure", "unit_price",
            "discount_percent", "discount_amount",
            "tax_rate", "tax_amount", "line_subtotal", "line_total",
            "sale_line", "returnable_quantity",
        ]
        # unit_cost is deliberately excluded: it is margin data and must not
        # reach a customer-facing document or an unprivileged client.
        read_only_fields = fields

    def _matching_sale_line(self, line: InvoiceLine):
        """
        The sale line this invoice line was billed from, or None.

        `sale_lines_by_product` is supplied by the view; without it — a list
        response, or an invoice with no sale — there is nothing to match and
        the fields simply come back null.
        """
        by_product = self.context.get("sale_lines_by_product")
        if not by_product or line.product_id is None:
            return None
        return by_product.get(line.product_id)

    def get_sale_line(self, line: InvoiceLine) -> int | None:
        sale_line = self._matching_sale_line(line)
        return sale_line.pk if sale_line else None

    def get_returnable_quantity(self, line: InvoiceLine) -> str | None:
        sale_line = self._matching_sale_line(line)
        if sale_line is None:
            return None
        return str(sale_line.quantity - sale_line.quantity_returned)


class CorrectionSummarySerializer(serializers.ModelSerializer):
    """A credit/debit note as it appears on the invoice it corrects."""

    total_amount = MoneySerializerField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "invoice_number", "invoice_type", "status",
            "invoice_date", "total_amount", "credit_reason_code", "notes",
        ]
        read_only_fields = fields


class InvoiceAllocationSerializer(serializers.ModelSerializer):
    """
    An allocation seen from the invoice's side.

    The mirror of `PaymentAllocationSerializer`: that one answers "which
    invoices did this payment settle", this one answers "what settled this
    invoice". Both directions are needed because the link is many-to-many —
    one transfer can clear six invoices, and one invoice can be cleared by
    several instalments.

    `method` is carried because it is the honest distinction between an
    invoice settled with money and one settled by a credit note. A credit note
    is applied through a Payment carrying method=CREDIT_NOTE, so the two are
    indistinguishable by amount alone, and calling both "paid" misstates what
    happened.
    """

    payment_reference = serializers.CharField(
        source="payment.reference", read_only=True,
    )
    payment_date = serializers.DateTimeField(source="payment.payment_date", read_only=True)
    method = serializers.CharField(source="payment.method", read_only=True)
    bank_reference = serializers.CharField(
        source="payment.bank_reference", read_only=True, default="",
    )
    # Reversal deletes the allocation rows, so in practice this is False for
    # every row rendered here. It is carried anyway because the flag lives on
    # the payment rather than the allocation: a payment reversed between this
    # response being built and read would otherwise be presented as settling
    # the invoice with no indication the money had gone back out.
    is_reversed = serializers.BooleanField(source="payment.is_reversed", read_only=True)
    received_by_name = serializers.CharField(
        source="payment.received_by.get_full_name", read_only=True, default="",
    )
    amount = MoneySerializerField(read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = [
            "id", "payment", "payment_reference", "payment_date", "method",
            "bank_reference", "is_reversed", "received_by_name",
            "amount", "created_at",
        ]
        read_only_fields = fields


class InvoiceListSerializer(serializers.ModelSerializer):
    total_amount = MoneySerializerField(read_only=True)
    balance_due = MoneySerializerField(read_only=True)
    paid_amount = MoneySerializerField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    days_overdue = serializers.IntegerField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "invoice_number", "invoice_type", "status",
            "customer", "customer_name", "customer_nif",
            "invoice_date", "due_date", "is_credit_sale",
            "total_amount", "paid_amount", "balance_due",
            "is_overdue", "days_overdue",
            # Surfaced in the list so an undeclared document is visible
            # without opening it — the whole point of tracking it separately.
            "fiscal_status",
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    customer_code = serializers.CharField(source="customer.customer_code", read_only=True)
    sale_number = serializers.CharField(source="sale.sale_number", read_only=True, default=None)
    # Present when this invoice records a cash sale that was already receipted.
    # The UI reads it to say so on the document rather than letting the invoice
    # look like an unpaid demand for money that has in fact been collected.
    source_receipt_number = serializers.CharField(
        source="source_receipt.receipt_number", read_only=True, default=None,
    )
    original_invoice_number = serializers.CharField(
        source="original_invoice.invoice_number", read_only=True, default=None,
    )
    # The receipt raised when this invoice was settled in full, so the detail
    # page can offer it directly instead of making the user search the receipt
    # list for the invoice they are already looking at.
    payment_receipt_id = serializers.UUIDField(
        source="payment_receipt.id", read_only=True, default=None,
    )
    payment_receipt_number = serializers.CharField(
        source="payment_receipt.receipt_number", read_only=True, default=None,
    )
    posted_by_name = serializers.CharField(
        source="posted_by.get_full_name", read_only=True, default="",
    )

    subtotal = MoneySerializerField(read_only=True)
    discount_amount = MoneySerializerField(read_only=True)
    taxable_amount = MoneySerializerField(read_only=True)
    tax_amount = MoneySerializerField(read_only=True)
    total_amount = MoneySerializerField(read_only=True)
    paid_amount = MoneySerializerField(read_only=True)
    balance_due = MoneySerializerField(read_only=True)

    is_editable = serializers.BooleanField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    days_overdue = serializers.IntegerField(read_only=True)
    payment_progress = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True,
    )
    is_declared = serializers.BooleanField(read_only=True)
    declaration_exhausted = serializers.BooleanField(read_only=True)

    # Credit/debit notes already issued against this invoice. Surfaced so the
    # detail page can show what has been corrected without a second request,
    # and so a second correction is a deliberate act rather than an accident.
    corrections = serializers.SerializerMethodField()

    # What actually settled this invoice, newest first. Without it the detail
    # page could show a balance but not how it was arrived at, and answering
    # "which payments were applied to this invoice?" meant leaving the screen
    # and scanning the payments list for this invoice number.
    payment_allocations = serializers.SerializerMethodField()

    # True when at least one allocation is a credit-note offset. The client
    # cannot derive this from `balance_due`, which is zero either way, and the
    # distinction matters: an invoice cleared by a correction was not paid.
    settled_by_credit_note = serializers.SerializerMethodField()

    @extend_schema_field(CorrectionSummarySerializer(many=True))
    def get_corrections(self, invoice: Invoice) -> list[dict]:
        notes = [
            note for note in invoice.corrections.all() if note.deleted_at is None
        ]
        return CorrectionSummarySerializer(notes, many=True).data

    @extend_schema_field(InvoiceAllocationSerializer(many=True))
    def get_payment_allocations(self, invoice: Invoice) -> list[dict]:
        # Ordered in Python rather than by the ORM so the prefetch on the
        # viewset is reused; re-ordering in the query would discard it and
        # reintroduce a per-invoice round trip.
        allocations = sorted(
            invoice.payment_allocations.all(),
            key=lambda allocation: allocation.payment.payment_date,
            reverse=True,
        )
        return InvoiceAllocationSerializer(allocations, many=True).data

    def get_settled_by_credit_note(self, invoice: Invoice) -> bool:
        from .models import PaymentMethod

        return any(
            allocation.payment.method == PaymentMethod.CREDIT_NOTE
            and not allocation.payment.is_reversed
            for allocation in invoice.payment_allocations.all()
        )

    class Meta:
        model = Invoice
        fields = [
            "id", "invoice_number", "invoice_type", "status",
            "customer", "customer_code",
            "customer_name", "customer_nif", "customer_address", "customer_phone",
            "sale", "sale_number",
            "source_receipt", "source_receipt_number",
            "invoice_date", "due_date", "payment_terms_days",
            "subtotal", "discount_amount", "taxable_amount", "tax_amount",
            "total_amount", "paid_amount", "balance_due", "currency",
            "is_credit_sale", "reference", "notes",
            "posted_at", "posted_by", "posted_by_name",
            "cancelled_at", "cancellation_reason",
            "print_count", "last_printed_at", "emailed_at",
            "original_invoice", "original_invoice_number",
            "payment_receipt_id", "payment_receipt_number",
            "credit_reason_code", "corrections",
            "payment_allocations", "settled_by_credit_note",
            "is_editable", "is_overdue", "days_overdue", "payment_progress",
            # --- OBR declaration ---
            # `last_declaration_error` is included because the person who can
            # act on a rejection needs to see why it was refused without
            # having to read the server logs.
            "fiscal_status", "fiscal_signature",
            "obr_registered_number", "obr_registered_at", "obr_electronic_signature",
            "declared_at", "declaration_attempts", "last_declaration_error",
            "is_declared", "declaration_exhausted",
            "lines", "created_at", "updated_at",
        ]
        read_only_fields = fields


class InvoiceLineWriteSerializer(serializers.Serializer):
    product = serializers.UUIDField(required=False, allow_null=True)
    description = serializers.CharField(max_length=255)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0"))
    discount_percent = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=Decimal("0"), max_value=Decimal("100"), required=False,
    )
    tax_rate = serializers.DecimalField(
        max_digits=7, decimal_places=4, min_value=Decimal("0"), required=False,
    )
    batch_numbers = serializers.CharField(max_length=255, required=False, allow_blank=True)
    unit_of_measure = serializers.CharField(max_length=16, required=False, allow_blank=True)


class InvoiceCreateSerializer(serializers.Serializer):
    customer = serializers.UUIDField()
    lines = InvoiceLineWriteSerializer(many=True, allow_empty=False)
    is_credit_sale = serializers.BooleanField(default=False)
    invoice_date = serializers.DateTimeField(required=False)
    reference = serializers.CharField(max_length=64, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class CreditNoteLineWriteSerializer(InvoiceLineWriteSerializer):
    """
    A credit note line, optionally carrying the goods-return decision.

    `sale_line` and `restock` are only meaningful when the credit note is
    correcting a physical return: they identify the batch the units came from
    and whether those units may re-enter sellable stock. `restock` defaults to
    False for the same reason as on the sale return path — returned medicines
    are quarantined unless storage integrity was demonstrably maintained.
    """

    sale_line = serializers.IntegerField(required=False, allow_null=True)
    restock = serializers.BooleanField(default=False)
    condition_notes = serializers.CharField(max_length=255, required=False, allow_blank=True)


class CreditNoteSerializer(serializers.Serializer):
    lines = CreditNoteLineWriteSerializer(many=True, allow_empty=False)
    reason = serializers.CharField(max_length=255)
    # The scenario that prompted the correction. Kept separate from the
    # free-text reason so the goods-return path can be recognised without
    # parsing prose, and so corrections stay reportable by cause.
    reason_code = serializers.ChoiceField(
        choices=CreditNoteReason.choices, default=CreditNoteReason.OTHER,
    )


class CancelInvoiceSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class PaymentAllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    amount = MoneySerializerField(read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ["id", "invoice", "invoice_number", "amount", "created_at"]
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.business_name", read_only=True)
    customer_code = serializers.CharField(source="customer.customer_code", read_only=True)
    allocations = PaymentAllocationSerializer(many=True, read_only=True)
    received_by_name = serializers.CharField(
        source="received_by.get_full_name", read_only=True, default="",
    )

    amount = MoneySerializerField(read_only=True)
    allocated_amount = MoneySerializerField(read_only=True)
    unallocated_amount = MoneySerializerField(read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id", "reference", "customer", "customer_code", "customer_name",
            "payment_date", "amount", "allocated_amount", "unallocated_amount",
            "method", "bank_reference", "received_by", "received_by_name",
            "notes", "is_reversed", "reversal_reason",
            "allocations", "created_at",
        ]
        read_only_fields = fields


class PaymentCreateSerializer(serializers.Serializer):
    customer = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0.0001"))
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    payment_date = serializers.DateTimeField(required=False)
    bank_reference = serializers.CharField(max_length=64, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    # When omitted, allocation is oldest-due-first — the standard commercial
    # convention and the one that minimises the customer's ageing profile.
    invoice_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True,
    )


class AllocatePaymentSerializer(serializers.Serializer):
    """
    Direct a payment's unallocated remainder at specific invoices.

    When `invoice_ids` is omitted the remainder settles the customer's open
    invoices oldest-due-first, the same order the automatic drawdown uses.
    """

    invoice_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True,
    )


class ReversePaymentSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class PaymentReceiptSerializer(serializers.ModelSerializer):
    """
    A payment receipt, with the invoice figures it acknowledges.

    The amounts are read-only properties resolving to the invoice, so this
    cannot report a total that disagrees with the document it settles.
    """

    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    invoice_date = serializers.DateTimeField(source="invoice.invoice_date", read_only=True)
    customer_code = serializers.CharField(source="customer.customer_code", read_only=True)
    issued_by_name = serializers.CharField(
        source="issued_by.get_full_name", read_only=True, default="",
    )
    payment_method = serializers.CharField(
        source="settling_payment.method", read_only=True, default="",
    )
    payment_reference = serializers.CharField(
        source="settling_payment.reference", read_only=True, default="",
    )

    amount_paid = MoneySerializerField(read_only=True)
    invoice_total = MoneySerializerField(read_only=True)
    is_cancelled = serializers.BooleanField(read_only=True)

    class Meta:
        model = PaymentReceipt
        fields = [
            "id", "receipt_number", "invoice", "invoice_number", "invoice_date",
            "customer", "customer_code", "customer_name", "customer_nif",
            "issued_at", "issued_by", "issued_by_name",
            "settling_payment", "payment_method", "payment_reference",
            "amount_paid", "invoice_total",
            "emailed_at", "print_count", "last_printed_at",
            "cancelled_at", "cancellation_reason", "is_cancelled",
            "created_at",
        ]
        read_only_fields = fields
