import logging

from celery import shared_task

from .audit import verify_chain

logger = logging.getLogger("security")


@shared_task(name="apps.core.tasks.verify_audit_chain")
def verify_audit_chain() -> dict:
    """
    Nightly tamper-evidence check.

    A break here means audit history was altered outside the application —
    the triggers were dropped or the table was edited directly. That is a
    security incident, not a bug, so it is logged at CRITICAL on the security
    channel and raised as a system notification for administrators.
    """
    result = verify_chain()

    if result["valid"]:
        logger.info("audit_chain_verified", extra={"entries_checked": result["checked"]})
        return result

    logger.critical(
        "audit_chain_broken",
        extra={
            "broken_at_id": result["broken_at_id"],
            "reason": result["reason"],
            "entries_checked": result["checked"],
        },
    )

    # Imported here to avoid a circular import at module load.
    from apps.notifications.services import notify_administrators

    notify_administrators(
        code="AUDIT_CHAIN_BROKEN",
        title="Audit trail integrity failure",
        body=(
            f"The audit hash chain failed verification at entry "
            f"{result['broken_at_id']}: {result['reason']}. "
            "Audit history may have been altered outside the application."
        ),
        severity="CRITICAL",
    )
    return result
