"""
Sales orchestration.

`confirm_sale` is the most consequential function in the system: it checks
credit, issues stock via FIFO, records batch traceability, raises the closing
document and updates the customer balance â€” all in one transaction. Either the
whole commercial event happens or none of it does. A partial commit here would
mean stock left the warehouse undocumented, or a document existed for goods
never issued.

Which document closes the sale depends on how it is paid:

    Cash sale    -> SalesReceipt   (proof of payment; the sale is settled)
    Credit sale  -> Invoice        (open receivable; settled later by payment)

Never both by default. A cash sale that also needs an invoice — because the
customer or a tax rule demands one — gets it from `invoice_for_receipt`, which
issues a second *document* for the same transaction without a second posting
of stock, tax or revenue. Everything ledger-affecting happens once, in
`confirm_sale`, and nowhere else.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record, snapshot
from apps.core.exceptions import (
    BusinessRuleViolation,
    InvalidStateTransition,
)
from apps.core.models import AuditAction
from apps.core.money import compute_line, price_ex_tax, q_internal, sum_money
from apps.core.numbering import next_number
from apps.inventory.models import MovementType
from apps.inventory.services import (
    allocate_fifo,
    post_batch_movement,
    release_reservations,
)

from .models import (
    Sale,
    SaleLine,
    SaleLineBatch,
    SaleReturn,
    SaleReturnLine,
    SalesReceipt,
    SaleStatus,
    SaleType,
    TenderMethod,
)

ZERO = Decimal("0")


def max_manual_discount() -> Decimal:
    """The configured ceiling on a manually entered discount, as a percentage."""
    return q_internal(settings.MAX_MANUAL_DISCOUNT_PERCENT)


def _check_discount_limit(discount_percent: Decimal, *, actor, is_standing: bool = False) -> None:
    """
    Refuse a manual discount above the configured ceiling.

    Enforced here rather than in the serializer so every route into a sale is
    covered — the API, a management command, an import. A rule that only the
    HTTP layer knows about is one a background job can walk straight past.

    `is_standing` marks a discount that came from the customer record rather
    than the operator. Those are contractual terms agreed when the account was
    opened, already governed by `partners.change_customer`, and are not
    re-litigated at the counter: applying the ceiling to them would block
    ordinary sales to a customer whose negotiated rate is 15%.

    A user holding `sales.override_discount_limit` may exceed the ceiling. The
    override is not silent — `create_sale` and `update_sale` record it in the
    audit log, because a discount above policy is exactly the entry a later
    review looks for.
    """
    if is_standing or discount_percent <= ZERO:
        return

    ceiling = max_manual_discount()
    if discount_percent <= ceiling:
        return

    # No actor means a system/import context with no user to check a
    # permission against. Fail closed: an unattributed caller is precisely the
    # one that must not be able to grant an unlimited discount.
    if actor is not None and actor.has_perm_code("sales.override_discount_limit"):
        return

    raise BusinessRuleViolation(
        _(
            "A discount of %(requested)s%% exceeds the maximum of %(max)s%%. "
            "A manager or administrator must authorise this."
        )
        % {"requested": _fmt_percent(discount_percent), "max": _fmt_percent(ceiling)},
        code="discount_limit_exceeded",
        details={
            "requested_percent": str(discount_percent),
            "max_percent": str(ceiling),
            "required_permission": "sales.override_discount_limit",
        },
    )


def _fmt_percent(value: Decimal) -> str:
    """Trim trailing zeros so 10.0000 reads as '10' in an error message."""
    normalised = value.normalize()
    # normalize() renders small integers in scientific notation (1E+1);
    # quantising back to an integer exponent avoids '1E+1%' in the message.
    if normalised == normalised.to_integral_value():
        normalised = normalised.quantize(Decimal("1"))
    return f"{normalised}"


def _exceeds_discount_ceiling(sale: Sale) -> bool:
    """Whether any discount on this sale sits above the configured ceiling."""
    ceiling = max_manual_discount()
    if sale.discount_percent > ceiling:
        return True
    return any(line.discount_percent > ceiling for line in sale.lines.all())


def _invoice_lines_for(sale: Sale, lines=None) -> list[dict]:
    """
    Build invoice line payloads from a sale's own lines.

    Shared by the credit path (which invoices at confirmation) and the
    receipt-to-invoice path (which invoices later). Both must produce
    byte-identical lines: an invoice raised for a cash sale a week afterwards
    has to show the same prices, batches and VAT as the receipt the customer
    already holds. Building them in one place is what guarantees that.

    Amounts are taken from the sale line as stored, never recomputed from the
    catalogue — the price may have changed since.
    """
    lines = lines if lines is not None else sale.lines.select_related("product")

    payload = []
    for line in lines:
        allocations = line.batch_allocations.all()
        payload.append(
            {
                "product": line.product,
                "product_code": line.product.product_code,
                "description": line.product.display_name,
                "batch_numbers": ", ".join(a.batch_number for a in allocations),
                "expiry_dates": ", ".join(
                    a.expiry_date.strftime("%m/%Y") for a in allocations
                ),
                "quantity": line.quantity,
                "unit_of_measure": line.product.unit_of_measure.code,
                "unit_price": line.unit_price,
                "discount_percent": line.discount_percent,
                "tax_rate": line.tax_rate,
                "unit_cost": line.unit_cost,
            }
        )
    return payload


@transaction.atomic
def create_sale(
    *,
    customer,
    warehouse,
    lines: list[dict],
    sale_type: str = SaleType.CASH,
    salesperson=None,
    sale_date=None,
    discount_percent: Decimal = ZERO,
    customer_order_reference: str = "",
    notes: str = "",
    actor=None,
) -> Sale:
    """
    Create a DRAFT sale.

    No stock moves and no invoice is produced here â€” a draft is a quotation
    in effect. Everything commercial happens at confirm_sale.

    Each line dict: product, quantity, unit_price (optional â€” resolved from
    the catalogue), discount_percent (optional).
    """
    if not lines:
        raise BusinessRuleViolation(
            _("A sale must contain at least one line."), code="empty_sale"
        )

    sale_date = sale_date or timezone.now()

    # The header discount is always operator-entered — there is no standing
    # equivalent at sale level — so it is checked unconditionally.
    discount_percent = q_internal(discount_percent)
    _check_discount_limit(discount_percent, actor=actor)

    # Supplying a pharmacy whose operating licence has lapsed exposes the
    # wholesaler to regulatory action, so it is refused at creation rather
    # than discovered at delivery.
    if customer.licence_is_expired:
        raise BusinessRuleViolation(
            _("The operating licence for %(name)s expired on %(date)s. The sale cannot proceed until it is renewed.")
            % {"name": customer.business_name, "date": customer.licence_expiry},
            code="customer_licence_expired",
            details={"licence_expiry": str(customer.licence_expiry)},
        )

    sale = Sale.objects.create(
        sale_number=next_number("sales.order", when=sale_date),
        sale_type=sale_type,
        status=SaleStatus.DRAFT,
        customer=customer,
        warehouse=warehouse,
        sale_date=sale_date,
        discount_percent=discount_percent,
        salesperson=salesperson or actor,
        delivery_address=customer.address,
        customer_order_reference=customer_order_reference,
        notes=notes,
        created_by=actor,
    )

    for index, data in enumerate(lines, start=1):
        _build_line(sale, index, data, customer, actor=actor)

    _recalculate_sale(sale)
    sale.save()

    record(
        AuditAction.CREATE,
        Sale._meta.label,
        entity_id=str(sale.pk),
        entity_label=sale.sale_number,
        new_value=snapshot(sale),
        # A discount above policy passed the gate above, which means someone
        # with override authority granted it. Flagged here so it is findable
        # in the audit log without re-deriving the ceiling from every row.
        notes=(
            f"Draft sale created with a discount above the "
            f"{_fmt_percent(max_manual_discount())}% ceiling"
            if _exceeds_discount_ceiling(sale)
            else ""
        ),
        actor=actor,
    )
    return sale


@transaction.atomic
def update_sale(
    sale: Sale,
    *,
    lines: list[dict] | None = None,
    customer=None,
    warehouse=None,
    sale_type: str | None = None,
    sale_date=None,
    discount_percent: Decimal | None = None,
    customer_order_reference: str | None = None,
    notes: str | None = None,
    actor=None,
) -> Sale:
    """
    Amend a draft sale.

    Only a DRAFT may be amended. Once confirmed, stock has moved and a document
    exists in the customer's hands; from that point the sale is corrected by
    cancellation or a return, never by editing what it says. That is the same
    rule `Invoice.is_editable` encodes, and it is checked here rather than in
    the API so no caller can route around it.

    Lines are replaced wholesale rather than diffed. A draft holds no batch
    allocations and no stock movements — nothing downstream references a line
    row — so rebuilding them is both correct and far simpler than reconciling
    an edit set. Any header field left as None keeps its current value, so a
    caller may send just the field it changed.

    Any reservations the draft held are released before rebuilding, otherwise
    an edit that removes a line would leave its stock reserved indefinitely.
    """
    if sale.status != SaleStatus.DRAFT:
        raise InvalidStateTransition(
            _("Only a draft sale can be edited; this one is %(status)s.")
            % {"status": sale.get_status_display()},
            details={"current_status": sale.status},
        )

    previous = snapshot(sale)

    if customer is not None:
        sale.customer = customer
        # The delivery address follows the customer unless the caller is also
        # changing it; a stale address from the previous customer would send
        # goods to the wrong pharmacy.
        sale.delivery_address = customer.address
    if warehouse is not None:
        sale.warehouse = warehouse
    if sale_type is not None:
        sale.sale_type = sale_type
    if sale_date is not None:
        sale.sale_date = sale_date
    if customer_order_reference is not None:
        sale.customer_order_reference = customer_order_reference
    if notes is not None:
        sale.notes = notes
    if discount_percent is not None:
        discount_percent = q_internal(discount_percent)
        _check_discount_limit(discount_percent, actor=actor)
        sale.discount_percent = discount_percent

    # Selling to a customer whose licence has since lapsed is refused on edit
    # for the same reason as on creation — the draft may have been raised
    # before the expiry date passed.
    if sale.customer.licence_is_expired:
        raise BusinessRuleViolation(
            _("The operating licence for %(name)s expired on %(date)s. The sale cannot proceed until it is renewed.")
            % {"name": sale.customer.business_name, "date": sale.customer.licence_expiry},
            code="customer_licence_expired",
            details={"licence_expiry": str(sale.customer.licence_expiry)},
        )

    if lines is not None:
        if not lines:
            raise BusinessRuleViolation(
                _("A sale must contain at least one line."), code="empty_sale"
            )

        # Release before rebuilding: the reservations belong to the lines
        # about to be discarded.
        release_reservations(source_type="sales.Sale", source_id=str(sale.pk))
        sale.lines.all().delete()

        for index, data in enumerate(lines, start=1):
            _build_line(sale, index, data, sale.customer, actor=actor)

    _recalculate_sale(sale)
    sale.updated_by = actor
    sale.save()

    record(
        AuditAction.UPDATE,
        Sale._meta.label,
        entity_id=str(sale.pk),
        entity_label=sale.sale_number,
        previous_value=previous,
        new_value=snapshot(sale),
        notes=(
            f"Draft sale amended with a discount above the "
            f"{_fmt_percent(max_manual_discount())}% ceiling"
            if _exceeds_discount_ceiling(sale)
            else "Draft sale amended"
        ),
        actor=actor,
    )
    return sale


@transaction.atomic
def delete_draft_sale(sale: Sale, *, reason: str = "", actor=None) -> Sale:
    """
    Delete a draft sale.

    Soft-deleted, like every other financial record: the row leaves the
    operator's view but stays on file, so a numbered document that once
    existed can still be accounted for. A gap in the sale series with nothing
    behind it is exactly what an inspection asks about.

    Restricted to drafts. A confirmed sale has moved stock and produced a
    document, and is voided by `cancel_sale`, which reverses both — deleting it
    would leave the warehouse short with no record of why.
    """
    if sale.status != SaleStatus.DRAFT:
        raise InvalidStateTransition(
            _("Only a draft sale can be deleted; this one is %(status)s. Cancel it instead.")
            % {"status": sale.get_status_display()},
            details={"current_status": sale.status},
        )

    # A draft may hold soft reservations against stock. Releasing them is the
    # whole point of deleting the draft — otherwise the goods stay unavailable
    # to other orders with nothing to show why.
    release_reservations(source_type="sales.Sale", source_id=str(sale.pk))

    sale.delete(actor=actor)

    record(
        AuditAction.DELETE,
        Sale._meta.label,
        entity_id=str(sale.pk),
        entity_label=sale.sale_number,
        previous_value={"status": SaleStatus.DRAFT, "total_amount": str(sale.total_amount)},
        notes=f"Draft sale deleted{f': {reason}' if reason else ''}",
        actor=actor,
    )
    return sale


def _build_line(sale: Sale, line_number: int, data: dict, customer, *, actor=None) -> SaleLine:
    """Create a sale line, resolving price and VAT from the catalogue."""
    product = data["product"]
    quantity = q_internal(data["quantity"])

    if quantity <= ZERO:
        raise BusinessRuleViolation(
            _("Line quantities must be greater than zero."), code="invalid_quantity"
        )

    tax_rate = q_internal(product.effective_vat_rate)

    # Price resolution order: explicit override -> customer-tier price.
    #
    # Both arrive TTC and are stored HT. The override is quoted at the counter
    # in the same terms the operator sees on screen and on the shelf edge — a
    # tax-inclusive figure — so it is extracted exactly like a catalogue price.
    # Treating it as already-HT would apply VAT a second time at compute_line
    # and undercharge the customer on every manually priced line.
    unit_price = data.get("unit_price")
    if unit_price is not None:
        unit_price = price_ex_tax(unit_price, tax_rate)
    else:
        unit_price = product.price_for(customer)

    # Line discount falls back to the customer's standing discount.
    discount_percent = data.get("discount_percent")
    is_standing = discount_percent is None
    if is_standing:
        discount_percent = customer.discount_percent
    discount_percent = q_internal(discount_percent)

    # Only a discount the operator typed is capped. The customer's standing
    # rate is a contractual term, not a counter decision — see
    # `_check_discount_limit`.
    _check_discount_limit(discount_percent, actor=actor, is_standing=is_standing)

    amounts = compute_line(quantity, unit_price, discount_percent, tax_rate)

    return SaleLine.objects.create(
        sale=sale,
        line_number=line_number,
        product=product,
        quantity=quantity,
        unit_price=unit_price,
        discount_percent=discount_percent,
        discount_amount=amounts["discount"],
        tax_rate=tax_rate,
        tax_amount=amounts["tax"],
        line_subtotal=amounts["gross"],
        line_total=amounts["total"],
        # unit_cost is filled at confirmation, once the actual batches are known.
        unit_cost=ZERO,
        line_cost=ZERO,
    )


def _recalculate_sale(sale: Sale) -> Sale:
    """Roll line amounts up to the header."""
    lines = list(sale.lines.all())
    sale.subtotal = sum_money(line.line_subtotal for line in lines)
    sale.discount_amount = sum_money(line.discount_amount for line in lines)
    sale.tax_amount = sum_money(line.tax_amount for line in lines)
    sale.total_amount = sum_money(line.line_total for line in lines)
    sale.total_cost = sum_money(line.line_cost for line in lines)
    return sale


@transaction.atomic
def confirm_sale(
    sale: Sale,
    *,
    actor=None,
    credit_override_reason: str = "",
    credit_override_by=None,
    generate_invoice: bool | None = None,
    payment_method: str = TenderMethod.CASH,
    payment_reference: str = "",
    amount_tendered: Decimal | None = None,
):
    """
    Confirm a sale: check credit, issue stock, record traceability, document it.

    The closing document depends on how the customer is paying, because the two
    cases are different commercial events:

    * **Cash sale** — settled at the counter. A `SalesReceipt` is issued and it
      is the only document; the money is already in, so there is nothing to
      invoice. Raising an invoice as well would double the sale in every
      report that reads invoices, and leave a fully-paid receivable sitting
      open against a customer who owes nothing.
    * **Credit sale** — payment comes later. A `SalesInvoice` is posted and
      stays open until settled, at which point `record_payment` produces the
      payment receipt that discharges it.

    Either way stock, VAT, revenue and cost are posted exactly once, here. The
    document is a *record* of that posting, not a second one — which is what
    lets a cash sale later acquire an invoice (`invoice_for_receipt`) without
    anything being counted twice.

    `generate_invoice` overrides the choice for callers with a standing reason
    to — a tax rule that demands an invoice on every sale, or a caller
    deferring the document. Left as None it follows the sale type, which is
    what every ordinary checkout wants.

    Ordering is deliberate. Credit is checked *before* stock moves, so a
    refused sale never leaves the warehouse short. Stock is issued before the
    document is raised, so no document can exist for goods that could not be
    supplied.
    """
    if sale.status != SaleStatus.DRAFT:
        raise InvalidStateTransition(
            _("Only a draft sale can be confirmed; this one is %(status)s.")
            % {"status": sale.get_status_display()},
            details={"current_status": sale.status},
        )

    lines = list(sale.lines.select_related("product"))
    if not lines:
        raise BusinessRuleViolation(
            _("A sale cannot be confirmed without lines."), code="empty_sale"
        )

    customer = sale.customer

    # --- 1. Credit gate -------------------------------------------------
    if sale.sale_type == SaleType.CREDIT:
        allowed, reason, code = customer.can_buy_on_credit(sale.total_amount)
        if not allowed:
            from apps.core.exceptions import CreditLimitExceeded

            details = {
                "customer_code": customer.customer_code,
                "credit_limit": str(customer.credit_limit),
                "outstanding_balance": str(customer.outstanding_balance),
                "available_credit": str(customer.available_credit),
                "sale_total": str(sale.total_amount),
            }

            # Only a genuine limit breach is an assumption of risk a supervisor
            # can take on. A blocked, cash-only or inactive account is a
            # standing decision about *whether* this customer may owe money at
            # all, and no amount of authority at the counter changes it — an
            # override there would silently reverse a credit decision made
            # elsewhere, which is precisely what the block exists to prevent.
            if code != "credit_limit_exceeded":
                raise CreditLimitExceeded(reason, code=code, details=details)

            if not credit_override_reason:
                raise CreditLimitExceeded(reason, code=code, details=details)
            # An override is a deliberate assumption of risk; it must be
            # attributable to a specific authoriser, not just "someone".
            if credit_override_by is None:
                raise BusinessRuleViolation(
                    _("A credit limit override must identify the authorising user."),
                    code="override_authoriser_required",
                )
            sale.credit_override_by = credit_override_by
            sale.credit_override_reason = credit_override_reason

    # --- 2. Issue stock, capturing real batch costs ---------------------
    for line in lines:
        allocations = allocate_fifo(
            line.product, line.quantity, warehouse=sale.warehouse, lock=True,
        )

        line_cost = ZERO
        for allocation in allocations:
            post_batch_movement(
                allocation.batch,
                MovementType.ISSUE,
                allocation.quantity,
                unit_cost=allocation.unit_cost,
                source_type="sales.Sale",
                source_id=str(sale.pk),
                source_reference=sale.sale_number,
                performed_by=actor,
                reason=f"Sale {sale.sale_number}",
            )
            SaleLineBatch.objects.create(
                sale_line=line,
                batch=allocation.batch,
                batch_number=allocation.batch.batch_number,
                expiry_date=allocation.batch.expiry_date,
                quantity=allocation.quantity,
                unit_cost=allocation.unit_cost,
            )
            line_cost = q_internal(line_cost + allocation.value)

        line.line_cost = line_cost
        line.unit_cost = q_internal(line_cost / line.quantity) if line.quantity else ZERO
        line.save(update_fields=["line_cost", "unit_cost"])

    # Any soft reservations this draft held are now realised.
    release_reservations(source_type="sales.Sale", source_id=str(sale.pk))

    _recalculate_sale(sale)
    sale.status = SaleStatus.CONFIRMED
    sale.confirmed_at = timezone.now()
    sale.updated_by = actor
    sale.save()

    # --- 3. Closing document --------------------------------------------
    # A cash sale is receipted; a credit sale is invoiced. Never both — see
    # the docstring. An explicit `generate_invoice` overrides the default.
    is_credit = sale.sale_type == SaleType.CREDIT
    wants_invoice = is_credit if generate_invoice is None else generate_invoice

    invoice = None
    receipt = None

    if wants_invoice:
        from apps.invoicing.services import create_invoice, post_invoice

        invoice = create_invoice(
            customer=customer,
            lines=_invoice_lines_for(sale, lines),
            is_credit_sale=is_credit,
            sale=sale,
            invoice_date=sale.sale_date,
            reference=sale.customer_order_reference,
            actor=actor,
        )
        post_invoice(invoice, actor=actor)

    # A cash sale is receipted even when an invoice was also demanded: the
    # money was taken at the counter and the receipt is what proves it. The
    # invoice above is then the fiscal record of that same settled sale, and
    # is linked to the receipt rather than standing as a separate one.
    if not is_credit:
        receipt = _issue_receipt(
            sale,
            payment_method=payment_method,
            payment_reference=payment_reference,
            amount_tendered=amount_tendered,
            actor=actor,
        )
        if invoice is not None:
            invoice.source_receipt = receipt
            # Posted invoices are immutable, but this link is part of
            # establishing the document's identity at issue rather than a
            # later edit of its contents — no amount, party or line changes.
            invoice.save(update_fields=["source_receipt", "updated_at"])

    # --- 4. Customer activity + exposure --------------------------------
    if customer.first_sale_at is None:
        customer.first_sale_at = sale.sale_date
    customer.last_sale_at = sale.sale_date
    customer.save(update_fields=["first_sale_at", "last_sale_at", "updated_at"])

    if sale.sale_type == SaleType.CREDIT:
        from apps.partners.services import recompute_balance

        recompute_balance(customer)

    record(
        AuditAction.POST,
        Sale._meta.label,
        entity_id=str(sale.pk),
        entity_label=sale.sale_number,
        new_value={
            "status": sale.status,
            "sale_type": sale.sale_type,
            "total_amount": str(sale.total_amount),
            "total_cost": str(sale.total_cost),
            "receipt": receipt.receipt_number if receipt else None,
            "invoice": invoice.invoice_number if invoice else None,
            "credit_override": bool(sale.credit_override_reason),
        },
        changed_fields=["status"],
        notes=(
            f"Sale confirmed: {sale.sale_number}"
            + (
                f" [CREDIT OVERRIDE by {credit_override_by}: {credit_override_reason}]"
                if sale.credit_override_reason
                else ""
            )
        ),
        actor=actor,
    )
    return sale, invoice, receipt


def _issue_receipt(
    sale: Sale,
    *,
    payment_method: str = TenderMethod.CASH,
    payment_reference: str = "",
    amount_tendered: Decimal | None = None,
    actor=None,
) -> SalesReceipt:
    """
    Issue the receipt for a settled cash sale.

    Called from inside `confirm_sale`'s transaction, after stock has moved and
    the sale totals are final. It posts nothing itself — the receipt is the
    record of a payment already taken, so there is no ledger effect left to
    apply here.

    `amount_tendered` is what the customer handed over, used to print the
    change due. It defaults to the exact total, which is the case for every
    non-cash tender (a card is charged the precise amount) and for a counter
    that does not track cash drawers.
    """
    total = sale.total_amount
    tendered = q_internal(amount_tendered) if amount_tendered is not None else total

    if tendered < total:
        raise BusinessRuleViolation(
            _("The amount tendered (%(tendered)s) is less than the total due (%(total)s).")
            % {"tendered": tendered, "total": total},
            code="insufficient_tender",
            details={"tendered": str(tendered), "total_due": str(total)},
        )

    receipt = SalesReceipt.objects.create(
        receipt_number=next_number("sales.sales_receipt", when=sale.sale_date),
        sale=sale,
        customer=sale.customer,
        # Frozen identity — see the model docstring.
        customer_name=sale.customer.business_name,
        customer_nif=sale.customer.nif,
        issued_at=sale.sale_date,
        payment_method=payment_method,
        payment_reference=payment_reference,
        amount_tendered=tendered,
        change_given=q_internal(tendered - total),
        issued_by=actor,
        created_by=actor,
    )

    record(
        AuditAction.POST,
        SalesReceipt._meta.label,
        entity_id=str(receipt.pk),
        entity_label=receipt.receipt_number,
        new_value={
            "sale": sale.sale_number,
            "customer": sale.customer.customer_code,
            "total_amount": str(total),
            "payment_method": payment_method,
        },
        notes=f"Cash sale receipted: {receipt.receipt_number}",
        actor=actor,
    )
    return receipt


@transaction.atomic
def invoice_for_receipt(receipt: SalesReceipt, *, reason: str = "", actor=None):
    """
    Raise an invoice for a sale that was already receipted at the counter.

    Covers both business cases, because they are the same operation: a customer
    or tax rule requiring an invoice for a cash sale, and a receipt being
    "converted" to an invoice after the fact. Nothing is converted in the
    literal sense — the receipt remains valid and remains the proof of payment.
    What happens is that a second *document* is issued describing the same
    transaction, linked back to the receipt.

    The invariants this exists to protect:

    * **No second sale.** The invoice is built from the original sale's lines,
      so there is exactly one commercial transaction throughout.
    * **No second inventory movement.** Stock was issued at confirmation. This
      function never touches `post_batch_movement`; the batch numbers it prints
      are read from the allocations already recorded.
    * **No second VAT or revenue posting.** Line amounts are copied from the
      sale as stored, not recomputed, and the invoice is created already
      settled — it never becomes an open receivable, so it cannot be counted
      as new income owed.
    * **Traceable.** The invoice carries `source_receipt`, and both documents
      point at the same sale.

    The invoice is posted and immediately marked PAID against the money the
    receipt already took. It is a fiscal record of a settled sale, not a
    request for payment, and leaving it open would misstate both the
    customer's balance and the receivables ledger.
    """
    if receipt.is_cancelled:
        raise BusinessRuleViolation(
            _("Receipt %(number)s has been cancelled and cannot be invoiced.")
            % {"number": receipt.receipt_number},
            code="receipt_cancelled",
        )

    sale = receipt.sale

    if sale.status == SaleStatus.CANCELLED:
        raise BusinessRuleViolation(
            _("Sale %(number)s has been cancelled and cannot be invoiced.")
            % {"number": sale.sale_number},
            code="sale_cancelled",
        )

    # The OneToOne on Invoice.sale makes a duplicate a database error; catching
    # it here turns that into an answer the operator can act on, and returns
    # the document they were asking for rather than failing.
    existing = getattr(sale, "invoice", None)
    if existing is not None:
        from apps.invoicing.models import InvoiceStatus

        if existing.status != InvoiceStatus.CANCELLED:
            raise BusinessRuleViolation(
                _("Sale %(sale)s has already been invoiced as %(invoice)s.")
                % {"sale": sale.sale_number, "invoice": existing.invoice_number},
                code="already_invoiced",
                details={
                    "invoice_id": str(existing.pk),
                    "invoice_number": existing.invoice_number,
                },
            )
        # A cancelled invoice leaves the sale invoiceable again, but the
        # OneToOne still holds the old row. Release it so the replacement can
        # take the link; the cancelled document is retained, as fiscal rules
        # require, and remains findable through its own number.
        existing.sale = None
        existing.source_receipt = None
        existing.save(update_fields=["sale", "source_receipt", "updated_at"])

    from apps.invoicing.models import InvoiceStatus
    from apps.invoicing.services import create_invoice, post_invoice

    invoice = create_invoice(
        customer=sale.customer,
        lines=_invoice_lines_for(sale),
        # False: the money is already in. Marking it a credit sale would give
        # it a due date and add it to the customer's outstanding balance —
        # inventing a debt that was settled before this document existed.
        is_credit_sale=False,
        sale=sale,
        # The invoice date is the date of the sale it records, not today. A
        # fiscal document must fall in the period the transaction occurred in,
        # otherwise the VAT lands in the wrong return.
        invoice_date=sale.sale_date,
        reference=receipt.receipt_number,
        notes=reason,
        actor=actor,
    )
    invoice.source_receipt = receipt
    invoice.save(update_fields=["source_receipt", "updated_at"])

    post_invoice(invoice, actor=actor)

    # Settle it against the money the receipt already took. No Payment row is
    # created: no new money arrived, and inventing one would overstate takings
    # for the day by the value of this sale.
    invoice.paid_amount = invoice.total_amount
    invoice.balance_due = ZERO
    invoice.status = InvoiceStatus.PAID
    invoice.save(
        update_fields=["paid_amount", "balance_due", "status", "updated_at"]
    )

    record(
        AuditAction.CREATE,
        SalesReceipt._meta.label,
        entity_id=str(receipt.pk),
        entity_label=receipt.receipt_number,
        new_value={
            "invoice": invoice.invoice_number,
            "sale": sale.sale_number,
            "total_amount": str(invoice.total_amount),
            "settled_by_receipt": True,
        },
        notes=(
            f"Invoice {invoice.invoice_number} issued against receipt "
            f"{receipt.receipt_number} for the same sale; no stock, tax or "
            f"revenue re-posted."
            + (f" Reason: {reason}" if reason else "")
        ),
        actor=actor,
    )
    return invoice


@transaction.atomic
def cancel_sale(sale: Sale, *, reason: str, actor=None) -> Sale:
    """
    Cancel a sale, returning any issued stock.

    A confirmed sale's stock is put back into the batches it came from, and
    its invoice is cancelled. Reversing the stock is what keeps the ledger
    honest â€” simply flagging the sale cancelled would leave phantom shortfalls.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to cancel a sale."), code="reason_required"
        )
    if sale.status in {SaleStatus.CANCELLED, SaleStatus.RETURNED}:
        raise InvalidStateTransition(
            _("This sale has already been cancelled or fully returned."),
            details={"status": sale.status},
        )

    previous_status = sale.status

    if sale.status != SaleStatus.DRAFT:
        for line in sale.lines.prefetch_related("batch_allocations"):
            for allocation in line.batch_allocations.all():
                outstanding = allocation.quantity - allocation.quantity_returned
                if outstanding <= ZERO:
                    continue
                from apps.inventory.models import StockBatch

                batch = StockBatch.objects.select_for_update().get(pk=allocation.batch_id)
                post_batch_movement(
                    batch,
                    MovementType.SALE_RETURN,
                    outstanding,
                    unit_cost=allocation.unit_cost,
                    source_type="sales.SaleCancellation",
                    source_id=str(sale.pk),
                    source_reference=sale.sale_number,
                    performed_by=actor,
                    reason=f"Sale cancelled: {reason}",
                )

        invoice = getattr(sale, "invoice", None)
        if invoice is not None:
            from apps.invoicing.models import InvoiceStatus
            from apps.invoicing.services import cancel_invoice

            if invoice.status != InvoiceStatus.CANCELLED:
                # An invoice raised against a receipt was settled from the
                # receipt's money rather than a Payment, so `cancel_invoice`
                # would refuse it as "already paid". Release that settlement
                # first: the money it represents is refunded through the
                # receipt being cancelled just below, not held against a
                # document that no longer exists.
                if invoice.source_receipt_id is not None and invoice.paid_amount > ZERO:
                    invoice.paid_amount = ZERO
                    invoice.balance_due = invoice.total_amount
                    invoice.status = InvoiceStatus.POSTED
                    invoice.save(
                        update_fields=[
                            "paid_amount", "balance_due", "status", "updated_at",
                        ]
                    )
                cancel_invoice(invoice, reason=f"Sale cancelled: {reason}", actor=actor)

        # The receipt is retained and stamped rather than deleted: it was
        # handed to a customer, and a document that existed must stay
        # findable — the same rule that keeps cancelled invoices on file.
        receipt = getattr(sale, "receipt", None)
        if receipt is not None and not receipt.is_cancelled:
            receipt.cancelled_at = timezone.now()
            receipt.cancellation_reason = reason
            receipt.updated_by = actor
            receipt.save(
                update_fields=[
                    "cancelled_at", "cancellation_reason", "updated_by", "updated_at",
                ]
            )
            record(
                AuditAction.CANCEL,
                SalesReceipt._meta.label,
                entity_id=str(receipt.pk),
                entity_label=receipt.receipt_number,
                new_value={"cancelled": True},
                notes=f"Receipt cancelled with its sale: {reason}",
                actor=actor,
            )

    release_reservations(source_type="sales.Sale", source_id=str(sale.pk))

    sale.status = SaleStatus.CANCELLED
    sale.cancelled_at = timezone.now()
    sale.cancellation_reason = reason
    sale.updated_by = actor
    sale.save(
        update_fields=["status", "cancelled_at", "cancellation_reason", "updated_by", "updated_at"]
    )

    if sale.sale_type == SaleType.CREDIT:
        from apps.partners.services import recompute_balance

        recompute_balance(sale.customer)

    record(
        AuditAction.CANCEL,
        Sale._meta.label,
        entity_id=str(sale.pk),
        entity_label=sale.sale_number,
        previous_value={"status": previous_status},
        new_value={"status": SaleStatus.CANCELLED},
        changed_fields=["status"],
        notes=f"Sale cancelled: {reason}",
        actor=actor,
    )
    return sale


@transaction.atomic
def process_return(
    sale: Sale,
    *,
    lines: list[dict],
    reason: str,
    actor=None,
    issue_credit_note: bool = True,
) -> SaleReturn:
    """
    Process a customer return.

    Each line dict: sale_line, batch (optional), quantity, restock (bool),
    condition_notes.

    `restock` defaults to False and must be set deliberately. Returned
    medicines re-enter sellable stock only when cold chain and storage
    integrity were maintained; otherwise they are quarantined for destruction.
    Getting this wrong is a patient-safety failure, not an accounting one.
    """
    if not reason or not reason.strip():
        raise BusinessRuleViolation(
            _("A reason is required to process a return."), code="reason_required"
        )
    if sale.status in {SaleStatus.DRAFT, SaleStatus.CANCELLED}:
        raise InvalidStateTransition(
            _("Returns can only be processed against a confirmed sale."),
            details={"status": sale.status},
        )

    sale_return = SaleReturn.objects.create(
        return_number=next_number("sales.return"),
        sale=sale,
        customer=sale.customer,
        reason=reason,
        processed_by=actor,
        created_by=actor,
    )

    total_refund = ZERO
    credit_lines = []

    for data in lines:
        sale_line: SaleLine = data["sale_line"]
        quantity = q_internal(data["quantity"])
        restock = bool(data.get("restock", False))

        if quantity <= ZERO:
            raise BusinessRuleViolation(
                _("A returned quantity must be greater than zero."), code="invalid_quantity"
            )

        returnable = sale_line.quantity - sale_line.quantity_returned
        if quantity > returnable:
            raise BusinessRuleViolation(
                _("Cannot return %(qty)s of %(product)s: only %(max)s remain returnable on this line.")
                % {
                    "qty": quantity,
                    "product": sale_line.product.name,
                    "max": returnable,
                },
                code="return_exceeds_sold",
            )

        # Default to the batch this line was actually issued from, so returned
        # units go back to the correct expiry and cost.
        allocation = (
            sale_line.batch_allocations.filter(batch_id=data["batch"].pk).first()
            if data.get("batch")
            else sale_line.batch_allocations.first()
        )
        if allocation is None:
            raise BusinessRuleViolation(
                _("No batch allocation was found for this sale line."),
                code="missing_batch_allocation",
            )

        refund = q_internal(quantity * sale_line.unit_price)
        # Discount given at sale must be honoured on the refund, otherwise the
        # customer is refunded more than they paid.
        if sale_line.discount_percent:
            refund = q_internal(refund * (1 - sale_line.discount_percent / Decimal("100")))

        SaleReturnLine.objects.create(
            sale_return=sale_return,
            sale_line=sale_line,
            batch=allocation.batch,
            quantity=quantity,
            unit_price=sale_line.unit_price,
            refund_amount=refund,
            restock=restock,
            condition_notes=data.get("condition_notes", ""),
        )

        from apps.inventory.models import StockBatch

        batch = StockBatch.objects.select_for_update().get(pk=allocation.batch_id)

        if restock:
            post_batch_movement(
                batch,
                MovementType.SALE_RETURN,
                quantity,
                unit_cost=allocation.unit_cost,
                source_type="sales.SaleReturn",
                source_id=str(sale_return.pk),
                source_reference=sale_return.return_number,
                performed_by=actor,
                reason=f"Customer return: {reason}",
            )
        else:
            # Not restocked: the units come back but are immediately written
            # off, so the ledger shows both the return and the destruction.
            post_batch_movement(
                batch,
                MovementType.SALE_RETURN,
                quantity,
                unit_cost=allocation.unit_cost,
                source_type="sales.SaleReturn",
                source_id=str(sale_return.pk),
                source_reference=sale_return.return_number,
                performed_by=actor,
                reason=f"Customer return (quarantined): {reason}",
            )
            post_batch_movement(
                batch,
                MovementType.DAMAGE,
                quantity,
                unit_cost=allocation.unit_cost,
                source_type="sales.SaleReturn",
                source_id=str(sale_return.pk),
                source_reference=sale_return.return_number,
                performed_by=actor,
                reason=(
                    f"Returned stock not fit for resale: "
                    f"{data.get('condition_notes') or reason}"
                ),
            )

        sale_line.quantity_returned = q_internal(sale_line.quantity_returned + quantity)
        sale_line.save(update_fields=["quantity_returned"])

        allocation.quantity_returned = q_internal(allocation.quantity_returned + quantity)
        allocation.save(update_fields=["quantity_returned"])

        total_refund = q_internal(total_refund + refund)
        credit_lines.append(
            {
                "product": sale_line.product,
                "product_code": sale_line.product.product_code,
                "description": f"{sale_line.product.display_name} â€” {_('return')}",
                "batch_numbers": allocation.batch_number,
                # Same format as the sale path above, so a credit note and the
                # invoice it corrects show the same batch and expiry side by
                # side — that pairing is what makes the two reconcilable
                # during a recall.
                "expiry_dates": allocation.expiry_date.strftime("%m/%Y"),
                "quantity": quantity,
                "unit_price": sale_line.unit_price,
                "discount_percent": sale_line.discount_percent,
                "tax_rate": sale_line.tax_rate,
                "unit_cost": allocation.unit_cost,
            }
        )

    sale_return.total_amount = total_refund
    sale_return.save(update_fields=["total_amount", "updated_at"])

    # Update the sale's return status.
    all_lines = list(sale.lines.all())
    fully_returned = all(line.quantity_returned >= line.quantity for line in all_lines)
    any_returned = any(line.quantity_returned > ZERO for line in all_lines)
    if fully_returned:
        sale.status = SaleStatus.RETURNED
    elif any_returned:
        sale.status = SaleStatus.PARTIALLY_RETURNED
    sale.save(update_fields=["status", "updated_at"])

    # A credit note corrects an invoice. A cash sale that was only receipted
    # has none, and the refund is settled at the counter against the receipt —
    # so there is nothing to credit and no note is raised. The stock movements
    # and the SaleReturn record above are the full trail in that case.
    if issue_credit_note and credit_lines:
        invoice = getattr(sale, "invoice", None)
        if invoice is not None:
            from apps.invoicing.services import issue_credit_note as _issue

            note = _issue(invoice, lines=credit_lines, reason=reason, actor=actor)
            sale_return.credit_note = note
            sale_return.save(update_fields=["credit_note", "updated_at"])

    record(
        AuditAction.UPDATE,
        SaleReturn._meta.label,
        entity_id=str(sale_return.pk),
        entity_label=sale_return.return_number,
        new_value={
            "sale": sale.sale_number,
            "total_refund": str(total_refund),
            "lines": len(credit_lines),
        },
        notes=f"Return processed: {reason}",
        actor=actor,
    )
    return sale_return


def register_receipt_print(receipt: SalesReceipt, *, actor=None) -> int:
    """
    Claim the next copy number for a receipt, and return it.

    Same contract as `invoicing.services.register_print`: the number is claimed
    by an atomic increment and read back, so two concurrent prints cannot both
    be recorded as copy #1. A receipt is the customer's proof of payment, and a
    reprint of one has to be as traceable as a reprint of an invoice.
    """
    from django.db.models import F

    receipt.print_count = F("print_count") + 1
    receipt.last_printed_at = timezone.now()
    receipt.save(update_fields=["print_count", "last_printed_at"])
    receipt.refresh_from_db(fields=["print_count"])
    copy_number = receipt.print_count

    record(
        AuditAction.PRINT,
        SalesReceipt._meta.label,
        entity_id=str(receipt.pk),
        entity_label=receipt.receipt_number,
        notes=f"Receipt printed (copy #{copy_number})",
        actor=actor,
    )
    return copy_number


def release_receipt_print(
    receipt: SalesReceipt, copy_number: int, *, reason: str, actor=None
) -> None:
    """
    Give back a copy number claimed for a receipt that was never produced.

    The claim is released rather than the audit entry deleted — the log is a
    hash chain, so the PRINT entry stays and a compensating note follows it.
    """
    from django.db.models import F

    updated = SalesReceipt.objects.filter(pk=receipt.pk, print_count__gt=0).update(
        print_count=F("print_count") - 1, updated_at=timezone.now()
    )
    if updated:
        receipt.refresh_from_db(fields=["print_count"])

    record(
        AuditAction.PRINT,
        SalesReceipt._meta.label,
        entity_id=str(receipt.pk),
        entity_label=receipt.receipt_number,
        notes=f"Receipt copy #{copy_number} released, not produced: {reason}",
        actor=actor,
    )


def trace_batch_recipients(batch_number: str) -> list[dict]:
    """
    Every customer who received units from a batch.

    The recall query. Ordered by sale date so the most recent shipments â€”
    the ones most likely still on a pharmacy's shelf â€” appear first.
    """
    allocations = (
        SaleLineBatch.objects.filter(batch_number=batch_number)
        .select_related(
            "sale_line__sale__customer", "sale_line__product", "sale_line__sale",
        )
        .order_by("-sale_line__sale__sale_date")
    )

    return [
        {
            "sale_number": a.sale_line.sale.sale_number,
            "sale_date": a.sale_line.sale.sale_date,
            "customer_code": a.sale_line.sale.customer.customer_code,
            "customer_name": a.sale_line.sale.customer.business_name,
            "customer_phone": a.sale_line.sale.customer.phone,
            "customer_email": a.sale_line.sale.customer.email,
            "product": a.sale_line.product.display_name,
            "batch_number": a.batch_number,
            "expiry_date": a.expiry_date,
            "quantity_supplied": a.quantity,
            "quantity_returned": a.quantity_returned,
            "quantity_outstanding": a.quantity - a.quantity_returned,
        }
        for a in allocations
    ]

