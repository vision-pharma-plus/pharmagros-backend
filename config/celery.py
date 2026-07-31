import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("pharmagros")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Scheduled operational jobs. Times are Africa/Bujumbura (CAT, UTC+2).
app.conf.beat_schedule = {
    # Expiry surveillance is the highest-value scheduled job in a pharma
    # wholesaler: stock silently becomes unsellable and legally undispensable.
    "scan-expiring-stock": {
        "task": "apps.inventory.tasks.scan_expiry_horizons",
        "schedule": crontab(hour=6, minute=0),
    },
    "scan-low-stock": {
        "task": "apps.inventory.tasks.scan_reorder_levels",
        "schedule": crontab(hour=6, minute=15),
    },
    # Quarantines batches that crossed expiry overnight so they can no longer
    # be picked by FIFO allocation.
    "quarantine-expired-batches": {
        "task": "apps.inventory.tasks.quarantine_expired_batches",
        "schedule": crontab(hour=0, minute=5),
    },
    "invoice-due-reminders": {
        "task": "apps.invoicing.tasks.send_due_reminders",
        "schedule": crontab(hour=7, minute=0),
    },
    "refresh-customer-balances": {
        "task": "apps.partners.tasks.recompute_outstanding_balances",
        "schedule": crontab(hour=1, minute=0),
    },
    "verify-audit-chain": {
        "task": "apps.core.tasks.verify_audit_chain",
        "schedule": crontab(hour=2, minute=0),
    },
}
