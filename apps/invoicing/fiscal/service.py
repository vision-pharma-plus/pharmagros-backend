"""
Declaration operations.

The rule this module exists to enforce: **posting never blocks on the OBR**.
A pharmacy counter in Bujumbura cannot stop serving because a link is down,
so posting assigns a signature locally, marks the document PENDING, and
returns. Everything reaching the network happens here, driven by a sweep.

Retries back off geometrically and stop at a configured ceiling. A document
the OBR will never accept — a malformed one, say — must not be retried until
the end of time; it is marked REJECTED and raised to a human instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record
from apps.core.exceptions import BusinessRuleViolation
from apps.core.models import AuditAction

from ..models import FiscalStatus, Invoice, InvoiceStatus, InvoiceType
from . import payload as payload_builder
from .client import OBRError, call, is_enabled  # noqa: F401  (re-exported)
from .signature import signature_for_invoice

logger = logging.getLogger(__name__)

# Documents that are never declared: a proforma is a quotation, not a sale.
NON_DECLARABLE_TYPES = {InvoiceType.PROFORMA}

# Geometric backoff, capped. The first retry is quick because the commonest
# failure is a brief link outage; later ones spread out so a sustained
# outage does not hammer the service.
BACKOFF_BASE = timedelta(minutes=5)
BACKOFF_CAP = timedelta(hours=6)


def is_declarable(invoice: Invoice) -> bool:
    """Whether this document is one the OBR expects to receive."""
    if invoice.invoice_type in NON_DECLARABLE_TYPES:
        return False

    start_date = settings.OBR["START_DATE"]
    if start_date:
        # Documents predating go-live were filed by whatever process came
        # before and must not be declared a second time.
        if invoice.invoice_date.strftime("%Y-%m-%d") < start_date:
            return False

    return True


def assign_signature(invoice: Invoice) -> str:
    """
    Compute and store the document's fiscal signature.

    Called from `post_invoice` inside its transaction. The signature is
    written once and then treated as immutable, like every other field of a
    posted document — recomputing it later could only ever produce a
    different identity for a document already in the customer's hands.
    """
    signature = signature_for_invoice(
        invoice,
        system_identifier=settings.OBR["SYSTEM_ID"],
        taxpayer_nif=settings.COMPANY["NIF"],
    )
    invoice.fiscal_signature = signature
    return signature


def backoff_for(attempts: int) -> timedelta:
    """
    Delay before the next attempt after `attempts` failures.

    The exponent is bounded before the multiplication rather than the result
    after it: timedelta multiplication overflows for large factors, so
    computing the raw delay first and then capping it would raise instead of
    returning the cap.
    """
    if attempts <= 0:
        return timedelta(0)

    max_doublings = 16  # 5 min << 16 already far exceeds the cap
    delay = BACKOFF_BASE * (2 ** min(attempts - 1, max_doublings))
    return min(delay, BACKOFF_CAP)


def is_due_for_retry(invoice: Invoice, *, now=None) -> bool:
    """
    Whether enough time has passed since the last failed attempt.

    A document that has never been attempted is always due.
    """
    if invoice.declaration_attempts == 0:
        return True
    reference = invoice.updated_at
    if reference is None:
        return True
    now = now or timezone.now()
    return now >= reference + backoff_for(invoice.declaration_attempts)


def declare_invoice(invoice: Invoice, *, actor=None) -> Invoice:
    """
    Submit one document to the OBR and record the outcome.

    Deliberately *not* wrapped in a single transaction. The network call sits
    in the middle, and the record of a failed attempt — the incremented
    counter, the error text, the request log — has to outlive the exception
    that reports it. One enclosing atomic block would roll all of that back
    on the way out, leaving no trace that the attempt ever happened, which is
    precisely the evidence an inspection asks for.

    Instead each state change is committed on its own, and the row is locked
    only for the two short critical sections that read-modify-write it, so
    two concurrent sweeps cannot declare the same document twice.
    """
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)

        if invoice.fiscal_status == FiscalStatus.DECLARED:
            return invoice
        if invoice.status == InvoiceStatus.DRAFT:
            raise BusinessRuleViolation(
                _("A draft invoice cannot be declared; post it first."),
                code="invoice_not_posted",
            )
        if not invoice.fiscal_signature:
            assign_signature(invoice)
            invoice.save(update_fields=["fiscal_signature", "updated_at"])

        body = payload_builder.build_declaration(
            invoice, signature=invoice.fiscal_signature
        )

    try:
        response = call("add_invoice", body, invoice=invoice, operation="declare")
    except OBRError as exc:
        _record_failure(invoice, exc, actor=actor)
        raise

    result = response.result
    # The acknowledgement carries the electronic signature at the top level in
    # some responses and inside `result` in others; accept either.
    envelope = response.body if isinstance(response.body, dict) else {}
    electronic_signature = (
        result.get("electronic_signature") or envelope.get("electronic_signature") or ""
    )

    # The OBR has accepted it; commit that fact under the lock so a
    # concurrent sweep sees the document as declared and leaves it alone.
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        invoice.fiscal_status = FiscalStatus.DECLARED
        invoice.declared_at = timezone.now()
        invoice.declaration_attempts += 1
        invoice.last_declaration_error = ""
        invoice.obr_registered_number = str(
            result.get("invoice_registered_number") or ""
        )[:64]
        invoice.obr_electronic_signature = str(electronic_signature)

        registered_date = result.get("invoice_registered_date")
        if registered_date:
            invoice.obr_registered_at = _parse_obr_datetime(registered_date)

        invoice.save(
            update_fields=[
                "fiscal_status", "declared_at", "declaration_attempts",
                "last_declaration_error", "obr_registered_number", "obr_registered_at",
                "obr_electronic_signature", "updated_at",
            ]
        )

    record(
        AuditAction.POST,
        Invoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.invoice_number,
        new_value={
            "fiscal_status": invoice.fiscal_status,
            "fiscal_signature": invoice.fiscal_signature,
            "obr_registered_number": invoice.obr_registered_number,
        },
        changed_fields=["fiscal_status"],
        notes=f"Declared to OBR: {invoice.invoice_number}",
        actor=actor,
    )
    return invoice


def _record_failure(invoice: Invoice, exc: OBRError, *, actor=None) -> None:
    """
    Note a failed attempt.

    A retryable failure leaves the document PENDING so the next sweep picks
    it up; a permanent one moves it to REJECTED immediately, because
    retrying a document the OBR has refused on its merits only delays the
    moment a human looks at it.
    """
    invoice.declaration_attempts += 1
    invoice.last_declaration_error = str(exc.message)[:5000]

    exhausted = invoice.declaration_attempts >= settings.OBR["MAX_ATTEMPTS"]
    if not exc.retryable or exhausted:
        invoice.fiscal_status = FiscalStatus.REJECTED
    else:
        invoice.fiscal_status = FiscalStatus.PENDING

    invoice.save(
        update_fields=[
            "fiscal_status", "declaration_attempts", "last_declaration_error", "updated_at",
        ]
    )

    logger.warning(
        "obr_declaration_failed",
        extra={
            "invoice": invoice.invoice_number,
            "attempts": invoice.declaration_attempts,
            "retryable": exc.retryable,
            "status_code": exc.status_code,
        },
    )

    if invoice.fiscal_status == FiscalStatus.REJECTED:
        record(
            AuditAction.REJECT,
            Invoice._meta.label,
            entity_id=str(invoice.pk),
            entity_label=invoice.invoice_number,
            new_value={
                "fiscal_status": invoice.fiscal_status,
                "attempts": invoice.declaration_attempts,
                "error": invoice.last_declaration_error[:500],
            },
            changed_fields=["fiscal_status"],
            notes=f"OBR declaration abandoned for {invoice.invoice_number}",
            actor=actor,
        )


def declare_cancellation(invoice: Invoice, *, reason: str = "", actor=None) -> Invoice:
    """
    Withdraw a previously declared document at the OBR.

    Only meaningful for a document the OBR has actually accepted: one still
    queued locally is simply dropped from the queue instead, since the OBR
    has no record to withdraw.

    Transaction handling follows `declare_invoice`: the network call sits
    outside any enclosing block so a failure leaves its trace behind.
    """
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)

        if invoice.fiscal_status != FiscalStatus.DECLARED:
            raise BusinessRuleViolation(
                _("Only a document already declared to the OBR can be withdrawn."),
                code="invoice_not_declared",
            )

        body = payload_builder.build_cancellation(invoice, reason=reason)

    try:
        call("cancel_invoice", body, invoice=invoice, operation="cancel")
    except OBRError as exc:
        invoice.last_declaration_error = str(exc.message)[:5000]
        invoice.save(update_fields=["last_declaration_error", "updated_at"])
        raise

    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        invoice.fiscal_status = FiscalStatus.CANCELLED
        invoice.cancellation_declared_at = timezone.now()
        invoice.last_declaration_error = ""
        invoice.save(
            update_fields=[
                "fiscal_status", "cancellation_declared_at",
                "last_declaration_error", "updated_at",
            ]
        )

    record(
        AuditAction.CANCEL,
        Invoice._meta.label,
        entity_id=str(invoice.pk),
        entity_label=invoice.invoice_number,
        new_value={"fiscal_status": invoice.fiscal_status},
        changed_fields=["fiscal_status"],
        notes=f"Cancellation declared to OBR: {invoice.invoice_number}",
        actor=actor,
    )
    return invoice


def pending_queue():
    """Documents awaiting declaration, oldest first — the OBR expects order."""
    return (
        Invoice.objects.filter(
            fiscal_status=FiscalStatus.PENDING,
            deleted_at__isnull=True,
        )
        .exclude(status=InvoiceStatus.DRAFT)
        .order_by("invoice_date")
    )


def stuck_queue():
    """Documents that have exhausted their retries and need a human."""
    return Invoice.objects.filter(
        fiscal_status=FiscalStatus.REJECTED,
        deleted_at__isnull=True,
    ).order_by("invoice_date")


def _parse_obr_datetime(value):
    """
    Parse a timestamp from the OBR, tolerating the formats it uses.

    Returns None rather than raising: a document that was accepted must not
    be recorded as failed merely because its acknowledgement carried a date
    in an unexpected shape.
    """
    from django.utils.dateparse import parse_datetime

    text = str(value).strip()
    parsed = parse_datetime(text)
    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        logger.warning("obr_unparseable_date", extra={"value": text[:64]})
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed
