"""
Inventory: warehouses, batches, and the stock ledger.

Central design decision — the stock ledger is an append-only journal, and
StockBatch.quantity_remaining is a derived cache of it. Every movement writes
a ledger row; nothing mutates quantities without one. This gives:

  * A provable reconstruction of stock at any past date (regulators ask
    "what did you hold on 12 March?" and the answer must be defensible).
  * A complete chain of custody per batch — required for a recall, where you
    must identify every customer who received units from a specific batch.
  * Detection of drift: if the cached quantity ever diverges from the sum of
    its ledger rows, that discrepancy is itself a reportable finding.

Storing only a mutable quantity column would make all three impossible.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, QuantityField
from apps.core.models import BaseModel, TimeStampedModel


class Warehouse(BaseModel):
    """
    A stock location.

    Present from day one even though the business starts single-site: adding
    a location dimension to an existing stock ledger later is a painful
    migration, whereas carrying one warehouse now costs almost nothing.
    """

    code = models.CharField(_("code"), max_length=16, unique=True)
    # Paired _fr/_en columns rather than gettext: a warehouse name is business
    # data an administrator edits at runtime, not a developer string in a .po
    # file. Same reasoning as catalog.Category.
    name_fr = models.CharField(_("name (French)"), max_length=120)
    name_en = models.CharField(_("name (English)"), max_length=120, blank=True)
    address = models.CharField(_("address"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=80, default="Bujumbura")
    is_default = models.BooleanField(_("default warehouse"), default=False)
    is_active = models.BooleanField(_("active"), default=True)
    # Cold-chain locations are tracked because storing a 2-8°C product in an
    # ambient location is a quality deviation, not merely a logistics detail.
    is_cold_chain = models.BooleanField(_("cold chain capable"), default=False)

    class Meta:
        verbose_name = _("warehouse")
        verbose_name_plural = _("warehouses")
        ordering = ("code",)
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True, deleted_at__isnull=True),
                name="unique_default_warehouse",
            )
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    @property
    def name(self) -> str:
        from apps.catalog.models import _localised

        return _localised(self, "name")

    def save(self, *args, **kwargs):
        from apps.core.translation import translate_text

        if self.name_fr and not self.name_en:
            self.name_en = translate_text(self.name_fr, "en")
        elif self.name_en and not self.name_fr:
            self.name_fr = translate_text(self.name_en, "fr")

        super().save(*args, **kwargs)


class BatchStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    QUARANTINED = "QUARANTINED", _("Quarantined")
    EXPIRED = "EXPIRED", _("Expired")
    DAMAGED = "DAMAGED", _("Damaged")
    RECALLED = "RECALLED", _("Recalled")
    DEPLETED = "DEPLETED", _("Depleted")
    DISPOSED = "DISPOSED", _("Disposed")


class StockBatch(BaseModel):
    """
    A quantity of one product, from one supplier delivery, with one expiry.

    The batch — not the product — is the unit of inventory truth in pharma.
    Two boxes of the same medicine with different expiry dates are different
    stock, must be valued at their own purchase cost, and must be issued in a
    defined order.
    """

    product = models.ForeignKey(
        "catalog.Medicine", on_delete=models.PROTECT, related_name="batches",
        verbose_name=_("product"),
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="batches", verbose_name=_("warehouse"),
    )
    batch_number = models.CharField(_("batch number"), max_length=64, db_index=True)

    manufacturing_date = models.DateField(_("manufacturing date"), null=True, blank=True)
    expiry_date = models.DateField(_("expiry date"), db_index=True)

    supplier = models.ForeignKey(
        "partners.Supplier", on_delete=models.PROTECT, related_name="supplied_batches",
        null=True, blank=True, verbose_name=_("supplier"),
    )
    purchase_order = models.ForeignKey(
        "purchasing.PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="batches", verbose_name=_("purchase order"),
    )

    quantity_received = QuantityField(_("quantity received"), validators=[MinValueValidator(Decimal("0"))])
    # Denormalised running balance. Authoritative source is the ledger; this
    # column exists because FIFO allocation and stock listings would otherwise
    # aggregate the movement table on every read.
    quantity_remaining = QuantityField(_("quantity remaining"), db_index=True)
    # Soft reservation held by draft sales so two users cannot both promise
    # the last carton. Available = remaining - reserved.
    quantity_reserved = QuantityField(_("quantity reserved"))

    unit_cost = MoneyField(_("unit purchase cost"))
    landed_unit_cost = MoneyField(
        _("landed unit cost"),
        help_text=_("Unit cost including freight, duty and clearing charges."),
    )

    status = models.CharField(
        _("status"), max_length=16, choices=BatchStatus.choices,
        default=BatchStatus.ACTIVE, db_index=True,
    )
    received_at = models.DateTimeField(_("received at"), default=timezone.now, db_index=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("stock batch")
        verbose_name_plural = _("stock batches")
        ordering = ("expiry_date", "received_at")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "batch_number", "warehouse"],
                condition=Q(deleted_at__isnull=True),
                name="unique_batch_per_product_warehouse",
            ),
            # A negative balance is never legitimate; if application logic
            # ever allowed one it would corrupt valuation silently.
            models.CheckConstraint(
                condition=Q(quantity_remaining__gte=Decimal("0")),
                name="batch_quantity_remaining_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(quantity_reserved__gte=Decimal("0")),
                name="batch_quantity_reserved_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(expiry_date__gt=F("manufacturing_date"))
                | Q(manufacturing_date__isnull=True),
                name="batch_expiry_after_manufacture",
            ),
        ]
        indexes = [
            # The FIFO allocation query's exact access path.
            models.Index(
                fields=["product", "warehouse", "status", "expiry_date"],
                name="idx_batch_fifo_pick",
            ),
            models.Index(fields=["expiry_date", "status"], name="idx_batch_expiry_scan"),
        ]

    def __str__(self) -> str:
        return f"{self.product.product_code} / {self.batch_number} (exp. {self.expiry_date:%Y-%m-%d})"

    @property
    def quantity_available(self) -> Decimal:
        """Sellable quantity: on hand minus what draft documents hold."""
        return self.quantity_remaining - self.quantity_reserved

    @property
    def is_expired(self) -> bool:
        return self.expiry_date < timezone.localdate()

    @property
    def days_to_expiry(self) -> int:
        return (self.expiry_date - timezone.localdate()).days

    @property
    def stock_value(self) -> Decimal:
        return self.quantity_remaining * self.landed_unit_cost

    def is_issuable(self) -> bool:
        """Whether FIFO may allocate from this batch."""
        return (
            self.status == BatchStatus.ACTIVE
            and not self.is_expired
            and self.quantity_available > 0
        )

    def ledger_balance(self) -> Decimal:
        """
        Recompute the balance from the ledger.

        Used by the reconciliation report. A mismatch against
        quantity_remaining indicates either a bug or direct database
        manipulation — both worth surfacing loudly.
        """
        result = self.movements.aggregate(total=Sum("quantity_delta"))["total"]
        return result or Decimal("0")


class MovementType(models.TextChoices):
    RECEIPT = "RECEIPT", _("Goods receipt")
    ISSUE = "ISSUE", _("Sales issue")
    SALE_RETURN = "SALE_RETURN", _("Customer return")
    PURCHASE_RETURN = "PURCHASE_RETURN", _("Return to supplier")
    TRANSFER_OUT = "TRANSFER_OUT", _("Transfer out")
    TRANSFER_IN = "TRANSFER_IN", _("Transfer in")
    ADJUSTMENT_IN = "ADJUSTMENT_IN", _("Positive adjustment")
    ADJUSTMENT_OUT = "ADJUSTMENT_OUT", _("Negative adjustment")
    DAMAGE = "DAMAGE", _("Damage write-off")
    EXPIRY = "EXPIRY", _("Expiry write-off")
    DISPOSAL = "DISPOSAL", _("Disposal")
    OPENING = "OPENING", _("Opening balance")


# Movement types that decrease stock. Kept as data rather than scattered
# sign logic so a new movement type cannot be added without deciding this.
OUTBOUND_TYPES = frozenset({
    MovementType.ISSUE, MovementType.PURCHASE_RETURN, MovementType.TRANSFER_OUT,
    MovementType.ADJUSTMENT_OUT, MovementType.DAMAGE, MovementType.EXPIRY,
    MovementType.DISPOSAL,
})


class StockMovement(TimeStampedModel):
    """
    One immutable line of the stock journal.

    Append-only by policy and by database trigger (see migration). Corrections
    are made by posting a compensating movement, never by editing history —
    the same discipline as a financial ledger, and for the same reason.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    batch = models.ForeignKey(
        StockBatch, on_delete=models.PROTECT, related_name="movements", verbose_name=_("batch"),
    )
    product = models.ForeignKey(
        "catalog.Medicine", on_delete=models.PROTECT, related_name="movements",
        verbose_name=_("product"),
    )
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="movements", verbose_name=_("warehouse"),
    )

    movement_type = models.CharField(
        _("movement type"), max_length=20, choices=MovementType.choices, db_index=True,
    )
    # Signed: positive increases stock, negative decreases it. A single signed
    # column makes the balance a plain SUM() rather than a conditional
    # aggregate, which is both faster and harder to get wrong.
    quantity_delta = QuantityField(_("quantity change"))
    balance_after = QuantityField(_("batch balance after movement"))

    unit_cost = MoneyField(_("unit cost at movement"))
    total_value = MoneyField(_("movement value"))

    # Polymorphic link to whatever caused this movement (invoice, PO receipt,
    # adjustment). Kept as type+id strings rather than a GenericForeignKey to
    # avoid the content-type join on every ledger read.
    source_type = models.CharField(_("source document type"), max_length=64, blank=True, db_index=True)
    source_id = models.CharField(_("source document id"), max_length=64, blank=True, db_index=True)
    source_reference = models.CharField(_("source reference"), max_length=64, blank=True)

    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_movements",
        null=True, blank=True, verbose_name=_("performed by"),
    )
    performed_at = models.DateTimeField(_("performed at"), default=timezone.now, db_index=True)
    reason = models.CharField(_("reason"), max_length=255, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    class Meta:
        verbose_name = _("stock movement")
        verbose_name_plural = _("stock movements")
        ordering = ("-performed_at", "-created_at")
        indexes = [
            models.Index(fields=["product", "-performed_at"], name="idx_move_product_date"),
            models.Index(fields=["batch", "-performed_at"], name="idx_move_batch_date"),
            models.Index(fields=["source_type", "source_id"], name="idx_move_source"),
            models.Index(fields=["movement_type", "-performed_at"], name="idx_move_type_date"),
        ]

    def __str__(self) -> str:
        sign = "+" if self.quantity_delta >= 0 else ""
        return f"{self.movement_type} {sign}{self.quantity_delta} — {self.batch_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and StockMovement.objects.filter(pk=self.pk).exists():
            raise RuntimeError(
                "Stock movements are immutable. Post a compensating movement instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError(
            "Stock movements cannot be deleted. Post a compensating movement instead."
        )


class StockReservation(TimeStampedModel):
    """
    A temporary hold placed by a draft sales document.

    Reservations expire: an abandoned draft must not lock stock forever. The
    sweeper task releases anything past expires_at.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(StockBatch, on_delete=models.CASCADE, related_name="reservations")
    quantity = QuantityField(_("quantity"))
    source_type = models.CharField(max_length=64, db_index=True)
    source_id = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField(_("expires at"), db_index=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("stock reservation")
        verbose_name_plural = _("stock reservations")
        indexes = [models.Index(fields=["source_type", "source_id"])]

    @property
    def is_active(self) -> bool:
        return self.released_at is None and self.expires_at > timezone.now()
