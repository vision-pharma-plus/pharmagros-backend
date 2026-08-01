import logging
from decimal import Decimal

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


@shared_task(name="apps.invoicing.tasks.email_invoice")
def email_invoice(invoice_id: str, language: str = "fr") -> bool:
    """
    Email an invoice with its PDF attached.

    Runs off the request path because PDF rendering plus SMTP can take
    seconds — far longer than the 500 ms API budget.
    """
    from .models import Invoice
    from .pdf import render_invoice_pdf

    invoice = Invoice.objects.filter(pk=invoice_id).select_related("customer").first()
    if not invoice or not invoice.customer.email:
        return False

    try:
        with translation.override(language):
            context = {"invoice": invoice, "company": settings.COMPANY}
            subject = _("%(company)s : Invoice %(number)s") % {
                "company": settings.COMPANY["NAME"],
                "number": invoice.invoice_number,
            }
            message = EmailMultiAlternatives(
                subject=subject,
                body=render_to_string("emails/invoice.txt", context),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[invoice.customer.email],
            )
            message.attach_alternative(render_to_string("emails/invoice.html", context), "text/html")
            message.attach(
                f"{invoice.invoice_number}.pdf",
                render_invoice_pdf(invoice, language=language),
                "application/pdf",
            )
            message.send()

        invoice.emailed_at = timezone.now()
        invoice.save(update_fields=["emailed_at"])
        return True
    except Exception:
        logger.exception("invoice_email_failed", extra={"invoice_id": invoice_id})
        return False


@shared_task(name="apps.invoicing.tasks.declare_pending_invoices")
def declare_pending_invoices(limit: int = 100) -> dict:
    """
    Drain the queue of documents awaiting declaration to the OBR.

    This is the counterpart to posting never blocking on the network: the
    backlog that builds during an outage is worked off here. Documents are
    taken oldest-first and each is handled independently, so one that the
    OBR refuses does not stall the ones behind it.

    Runs every few minutes. `limit` bounds a single pass so a long backlog
    is spread over several runs rather than occupying a worker indefinitely.
    """
    from .fiscal import service as fiscal
    from .fiscal.client import OBRError

    if not fiscal.is_enabled():
        return {"skipped": "disabled"}

    declared = failed = deferred = 0

    for invoice in fiscal.pending_queue()[:limit]:
        # Respect the backoff: a document that failed minutes ago is left
        # alone so a sustained outage is not hammered every sweep.
        if not fiscal.is_due_for_retry(invoice):
            deferred += 1
            continue

        try:
            fiscal.declare_invoice(invoice)
            declared += 1
        except OBRError:
            # Already recorded against the invoice and the request log by
            # the service; counted here so the sweep can report on itself.
            failed += 1
        except Exception:
            failed += 1
            logger.exception(
                "obr_declaration_unexpected_error",
                extra={"invoice": invoice.invoice_number},
            )

    if declared or failed:
        logger.info(
            "obr_declaration_sweep",
            extra={"declared": declared, "failed": failed, "deferred": deferred},
        )

    return {"declared": declared, "failed": failed, "deferred": deferred}


@shared_task(name="apps.invoicing.tasks.declare_pending_cancellations")
def declare_pending_cancellations(limit: int = 50) -> dict:
    """
    Withdraw, at the OBR, documents cancelled locally after declaration.

    Kept separate from the submission sweep because the two fail for
    different reasons and a backlog of one should not delay the other.
    """
    from .fiscal import service as fiscal
    from .fiscal.client import OBRError

    from .models import FiscalStatus, Invoice, InvoiceStatus

    if not fiscal.is_enabled():
        return {"skipped": "disabled"}

    pending = Invoice.objects.filter(
        status=InvoiceStatus.CANCELLED,
        fiscal_status=FiscalStatus.DECLARED,
        deleted_at__isnull=True,
    ).order_by("cancelled_at")[:limit]

    withdrawn = failed = 0
    for invoice in pending:
        try:
            fiscal.declare_cancellation(invoice, reason=invoice.cancellation_reason)
            withdrawn += 1
        except OBRError:
            failed += 1
        except Exception:
            failed += 1
            logger.exception(
                "obr_cancellation_unexpected_error",
                extra={"invoice": invoice.invoice_number},
            )

    return {"withdrawn": withdrawn, "failed": failed}


@shared_task(name="apps.invoicing.tasks.alert_stuck_declarations")
def alert_stuck_declarations() -> dict:
    """
    Raise documents that have exhausted their retries.

    A document the OBR keeps refusing is a compliance exposure that grows
    quietly: nothing in the commercial workflow surfaces it, because the
    invoice is posted, printed and possibly paid. This is the signal that
    someone must intervene.
    """
    from apps.notifications.models import NotificationCode, NotificationSeverity
    from apps.notifications.services import notify_permission_holders

    from .fiscal import service as fiscal

    if not fiscal.is_enabled():
        return {"skipped": "disabled"}

    stuck = fiscal.stuck_queue()
    count = stuck.count()
    if not count:
        return {"stuck": 0}

    today = timezone.localdate()
    notify_permission_holders(
        permission_code="invoicing.declare_invoice",
        code=NotificationCode.OBR_DECLARATION_FAILED,
        title=str(
            _("%(count)d invoices could not be declared to the OBR") % {"count": count}
        ),
        body=str(
            _(
                "These documents have exhausted their retry attempts and remain "
                "undeclared. Review them and resubmit once the cause is corrected."
            )
        ),
        severity=NotificationSeverity.CRITICAL,
        link="/invoicing/invoices?fiscal_status=REJECTED",
        dedupe_key=f"obr-stuck-{today:%Y-%m-%d}",
    )

    logger.warning("obr_declarations_stuck", extra={"count": count})
    return {"stuck": count}


@shared_task(name="apps.invoicing.tasks.send_due_reminders")
def send_due_reminders() -> dict:
    """
    Flag overdue invoices and alert on those approaching their due date.

    Two distinct signals: an invoice becoming overdue is a collection event,
    while one due in a few days is a courtesy reminder. Conflating them would
    make the alert stream unreadable.
    """
    from apps.notifications.models import NotificationCode, NotificationSeverity
    from apps.notifications.services import notify_permission_holders

    from .models import Invoice, InvoiceStatus
    from .services import mark_overdue_invoices

    newly_overdue = mark_overdue_invoices()
    today = timezone.localdate()

    due_soon = Invoice.objects.filter(
        status__in=[InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID],
        due_date__gte=today,
        due_date__lte=today + timezone.timedelta(days=7),
        balance_due__gt=0,
        deleted_at__isnull=True,
    )

    overdue = Invoice.objects.filter(
        status=InvoiceStatus.OVERDUE, balance_due__gt=0, deleted_at__isnull=True,
    )

    if overdue.exists():
        total = sum(inv.balance_due for inv in overdue)
        notify_permission_holders(
            permission_code="invoicing.record_payment",
            code=NotificationCode.INVOICE_OVERDUE,
            title=str(
                _("%(count)d overdue invoices") % {"count": overdue.count()}
            ),
            body=str(
                _("Total overdue: %(total)s BIF. Review the receivables ageing report.")
                % {"total": total.quantize(Decimal("1"))}
            ),
            severity=NotificationSeverity.WARNING,
            link="/invoicing/invoices?overdue=true",
            dedupe_key=f"overdue-invoices-{today:%Y-%m-%d}",
        )

    if due_soon.exists():
        notify_permission_holders(
            permission_code="invoicing.record_payment",
            code=NotificationCode.INVOICE_DUE,
            title=str(
                _("%(count)d invoices due within 7 days") % {"count": due_soon.count()}
            ),
            body=str(_("Contact customers to arrange settlement.")),
            severity=NotificationSeverity.INFO,
            link="/invoicing/invoices?unpaid=true",
            dedupe_key=f"due-soon-invoices-{today:%Y-%W}",
        )

    logger.info(
        "due_reminders_complete",
        extra={"newly_overdue": newly_overdue, "due_soon": due_soon.count()},
    )
    return {
        "newly_marked_overdue": newly_overdue,
        "total_overdue": overdue.count(),
        "due_soon": due_soon.count(),
    }
