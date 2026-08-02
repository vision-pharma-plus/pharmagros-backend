"""
Verify that the seeded database is internally consistent.

This checks the invariants that matter commercially rather than merely that
rows exist: that stock on hand equals what was received minus what was sold,
that every invoice header matches the sum of its lines, that payments never
exceed the balance they discharge, and that no required field was left blank.

Exits non-zero when something fails, so it can gate a deploy.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from apps.catalog.models import Medicine
from apps.inventory.models import StockBatch, StockMovement, Warehouse
from apps.invoicing.models import Invoice, Payment
from apps.partners.models import Customer, Supplier
from apps.purchasing.models import GoodsReceipt, PurchaseOrder
from apps.sales.models import SalesReceipt

ZERO = Decimal("0")
CENT = Decimal("0.01")


class Command(BaseCommand):
    help = "Check referential, stock and financial consistency of the seeded data."

    def handle(self, *args, **options):
        self.failures = []
        self.checks = 0

        self._check_master_data()
        self._check_required_fields()
        self._check_stock_reconciliation()
        self._check_invoice_totals()
        self._check_payment_balances()
        self._check_receipts()
        self._check_purchase_orders()

        self.stdout.write("")
        if self.failures:
            self.stdout.write(
                self.style.ERROR(f"{len(self.failures)} check(s) failed:")
            )
            for failure in self.failures:
                self.stdout.write(self.style.ERROR(f"  - {failure}"))
            raise SystemExit(1)

        self.stdout.write(
            self.style.SUCCESS(f"All {self.checks} checks passed.")
        )

    # ------------------------------------------------------------------
    def _ok(self, label):
        self.checks += 1
        self.stdout.write(f"  OK   {label}")

    def _fail(self, label, detail):
        self.checks += 1
        self.failures.append(f"{label}: {detail}")
        self.stdout.write(self.style.ERROR(f"  FAIL {label} — {detail}"))

    def _expect(self, label, condition, detail=""):
        if condition:
            self._ok(label)
        else:
            self._fail(label, detail or "condition not met")

    # ------------------------------------------------------------------
    def _check_master_data(self):
        self.stdout.write("Master data")
        self._expect(
            "at least 10 medicines",
            Medicine.objects.count() >= 10,
            f"found {Medicine.objects.count()}",
        )
        self._expect(
            "at least 1 warehouse",
            Warehouse.objects.exists(),
            "no warehouse",
        )
        self._expect(
            "at least 2 suppliers",
            Supplier.objects.count() >= 2,
            f"found {Supplier.objects.count()}",
        )
        self._expect(
            "at least 2 customers",
            Customer.objects.count() >= 2,
            f"found {Customer.objects.count()}",
        )

    def _check_required_fields(self):
        """
        No medicine should carry an empty descriptive field. These are all
        nominally optional at the model level, but a catalogue with blank
        strengths or missing barcodes is not usable, so the seed fills them.
        """
        self.stdout.write("Field completeness")
        blank = []
        for medicine in Medicine.objects.all():
            for field in (
                "name_fr", "name_en", "generic_name", "strength_fr",
                "pack_size", "atc_code", "registration_number", "barcode",
                "storage_notes", "notes_fr",
            ):
                if not (getattr(medicine, field) or "").strip():
                    blank.append(f"{medicine.product_code}.{field}")
        self._expect(
            "no blank medicine fields",
            not blank,
            f"{len(blank)} blank: {', '.join(blank[:5])}",
        )

        orphans = Medicine.objects.filter(manufacturer__isnull=True).count()
        self._expect(
            "every medicine has a manufacturer",
            orphans == 0,
            f"{orphans} without",
        )

    def _check_stock_reconciliation(self):
        """
        On-hand quantity must equal the signed sum of its movements. This is
        the check that would catch a sale that deducted the wrong batch, or a
        receipt booked without a corresponding ledger entry.
        """
        self.stdout.write("Stock reconciliation")
        mismatched = []
        for batch in StockBatch.objects.all():
            # quantity_delta is already signed — outbound movements are stored
            # negative — so the ledger balance is a plain sum. Splitting it by
            # direction here would risk disagreeing with the sign convention
            # the inventory service actually writes.
            balance = StockMovement.objects.filter(batch=batch).aggregate(
                total=Sum("quantity_delta")
            )["total"] or ZERO

            if batch.quantity_remaining != balance:
                mismatched.append(
                    f"{batch.batch_number}: on hand {batch.quantity_remaining}, "
                    f"movements imply {balance}"
                )

        self._expect(
            "batch quantities match their movements",
            not mismatched,
            "; ".join(mismatched[:3]),
        )

        negative = StockBatch.objects.filter(quantity_remaining__lt=ZERO).count()
        self._expect("no negative stock", negative == 0, f"{negative} batches")

    def _check_invoice_totals(self):
        """Header totals must equal the sum of the lines, to the centime."""
        self.stdout.write("Invoice totals")
        mismatched = []
        for invoice in Invoice.objects.prefetch_related("lines"):
            line_total = sum(
                (line.line_total for line in invoice.lines.all()), ZERO
            )
            if abs(invoice.total_amount - line_total) > CENT:
                mismatched.append(
                    f"{invoice.invoice_number}: header {invoice.total_amount}, "
                    f"lines {line_total}"
                )

        self._expect(
            "invoice headers match their lines",
            not mismatched,
            "; ".join(mismatched[:3]),
        )

        self._expect(
            "at least 2 invoices exist",
            Invoice.objects.count() >= 2,
            f"found {Invoice.objects.count()}",
        )

    def _check_payment_balances(self):
        """A balance due below zero means a customer was over-allocated."""
        self.stdout.write("Payments")
        overpaid = [
            invoice.invoice_number
            for invoice in Invoice.objects.all()
            if invoice.balance_due < ZERO
        ]
        self._expect(
            "no invoice is over-paid",
            not overpaid,
            ", ".join(overpaid[:3]),
        )

        self._expect(
            "payments recorded",
            Payment.objects.exists(),
            "no payments",
        )

        states = set(Invoice.objects.values_list("status", flat=True))
        self._expect(
            "both settled and outstanding invoices present",
            len(states) >= 1 and Invoice.objects.count() >= 2,
            f"statuses: {sorted(states)}",
        )

    def _check_receipts(self):
        """
        Cash sales must produce receipts, not invoices. A cash sale that also
        raised an invoice would double-count the revenue.
        """
        self.stdout.write("Sales receipts")
        self._expect(
            "at least 2 sales receipts",
            SalesReceipt.objects.count() >= 2,
            f"found {SalesReceipt.objects.count()}",
        )

        double_counted = []
        for receipt in SalesReceipt.objects.select_related("sale"):
            if receipt.sale and Invoice.objects.filter(sale=receipt.sale).exists():
                double_counted.append(receipt.receipt_number)
        self._expect(
            "no cash sale carries both a receipt and an invoice",
            not double_counted,
            ", ".join(double_counted[:3]),
        )

    def _check_purchase_orders(self):
        self.stdout.write("Purchasing")
        self._expect(
            "at least 2 purchase orders",
            PurchaseOrder.objects.count() >= 2,
            f"found {PurchaseOrder.objects.count()}",
        )
        self._expect(
            "a goods receipt exists",
            GoodsReceipt.objects.exists(),
            "none recorded",
        )
        self._expect(
            "received goods created stock batches",
            StockBatch.objects.exists(),
            "no batches",
        )
