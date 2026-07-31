"""
Medicine master data.

The catalogue describes *what* a product is; apps.inventory tracks physical
units of it. Keeping the split strict matters: a price change or a
reclassification must never imply a stock movement, and vice versa.

Bilingual naming is handled with paired _fr/_en columns rather than gettext,
because these are business data an administrator edits at runtime, not
developer strings shipped in a .po file.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, PercentField, QuantityField
from apps.core.models import BaseModel


def _localised(instance, base: str) -> str:
    """Pick the _fr or _en variant of a paired field for the active language."""
    suffix = "fr" if (get_language() or "fr").startswith("fr") else "en"
    value = getattr(instance, f"{base}_{suffix}", "")
    # Fall back to French, which is always populated — an English-only label
    # would otherwise render blank for francophone staff.
    return value or getattr(instance, f"{base}_fr", "")


class Category(BaseModel):
    """
    Therapeutic or commercial classification.

    Self-referencing to allow a shallow tree (e.g. Anti-infectieux →
    Antibiotiques → Bêta-lactamines) without imposing a full MPTT dependency;
    depth in practice is 2-3 levels.
    """

    code = models.CharField(_("code"), max_length=24, unique=True)
    name_fr = models.CharField(_("name (French)"), max_length=120)
    name_en = models.CharField(_("name (English)"), max_length=120, blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="children", verbose_name=_("parent category"),
    )
    description_fr = models.TextField(_("description (French)"), blank=True)
    description_en = models.TextField(_("description (English)"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("category")
        verbose_name_plural = _("categories")
        ordering = ("code",)

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    @property
    def name(self) -> str:
        return _localised(self, "name")

    @property
    def description(self) -> str:
        return _localised(self, "description")

    def save(self, *args, **kwargs):
        from apps.core.translation import translate_text
        if self.name_fr and not self.name_en:
            self.name_en = translate_text(self.name_fr, "en")
        elif self.name_en and not self.name_fr:
            self.name_fr = translate_text(self.name_en, "fr")

        if self.description_fr and not self.description_en:
            self.description_en = translate_text(self.description_fr, "en")
        elif self.description_en and not self.description_fr:
            self.description_fr = translate_text(self.description_en, "fr")

        super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError

        node, seen = self.parent, {self.pk}
        while node is not None:
            if node.pk in seen:
                raise ValidationError({"parent": _("A category cannot be its own ancestor.")})
            seen.add(node.pk)
            node = node.parent


class Manufacturer(BaseModel):
    """
    Marketing authorisation holder / producer.

    Country is retained because import documentation and OBR customs
    declarations require the country of origin per product.
    """

    code = models.CharField(_("code"), max_length=24, unique=True)
    name = models.CharField(_("name"), max_length=150)
    country = models.CharField(_("country"), max_length=80, blank=True)
    contact_email = models.EmailField(_("email"), blank=True)
    contact_phone = models.CharField(_("telephone"), max_length=32, blank=True)
    website = models.URLField(_("website"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("manufacturer")
        verbose_name_plural = _("manufacturers")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class UnitOfMeasure(BaseModel):
    """
    Sellable unit, with an optional conversion to a base unit.

    A wholesaler buys cartons and may sell boxes; `units_per_pack` records the
    relationship so a carton receipt can be expressed in the stocking unit.
    Conversion is stored, never inferred — guessing that a "boîte" is 10 of
    something is how stock counts drift.
    """

    code = models.CharField(_("code"), max_length=16, unique=True)
    name_fr = models.CharField(_("name (French)"), max_length=60)
    name_en = models.CharField(_("name (English)"), max_length=60, blank=True)
    base_unit = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="derived_units", verbose_name=_("base unit"),
    )
    units_per_pack = QuantityField(
        _("units per pack"), default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("unit of measure")
        verbose_name_plural = _("units of measure")
        ordering = ("code",)

    def __str__(self) -> str:
        return self.code

    @property
    def name(self) -> str:
        return _localised(self, "name")


class DosageForm(models.TextChoices):
    TABLET = "TABLET", _("Tablet")
    CAPSULE = "CAPSULE", _("Capsule")
    SYRUP = "SYRUP", _("Syrup")
    SUSPENSION = "SUSPENSION", _("Suspension")
    INJECTION = "INJECTION", _("Injection")
    INFUSION = "INFUSION", _("Infusion")
    CREAM = "CREAM", _("Cream")
    OINTMENT = "OINTMENT", _("Ointment")
    GEL = "GEL", _("Gel")
    DROPS = "DROPS", _("Drops")
    SUPPOSITORY = "SUPPOSITORY", _("Suppository")
    INHALER = "INHALER", _("Inhaler")
    PATCH = "PATCH", _("Patch")
    POWDER = "POWDER", _("Powder")
    SOLUTION = "SOLUTION", _("Solution")
    DEVICE = "DEVICE", _("Medical device")
    CONSUMABLE = "CONSUMABLE", _("Consumable")
    OTHER = "OTHER", _("Other")


class StorageCondition(models.TextChoices):
    AMBIENT = "AMBIENT", _("Ambient (15–25 °C)")
    COOL = "COOL", _("Cool (8–15 °C)")
    REFRIGERATED = "REFRIGERATED", _("Refrigerated (2–8 °C)")
    FROZEN = "FROZEN", _("Frozen (below 0 °C)")
    PROTECT_LIGHT = "PROTECT_LIGHT", _("Protect from light")
    DRY = "DRY", _("Keep dry")


class ProductStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    DISCONTINUED = "DISCONTINUED", _("Discontinued")
    SUSPENDED = "SUSPENDED", _("Suspended")
    PENDING = "PENDING", _("Pending approval")


class Medicine(BaseModel):
    """
    A catalogue product.

    Note `requires_prescription` and `is_controlled`: controlled substances
    (psychotropics, narcotics) carry additional record-keeping duties under
    Burundian pharmacy regulation. Flagging them in master data lets the sales
    and reporting layers apply the stricter rules without hard-coding a list
    of product names.
    """

    product_code = models.CharField(_("product code"), max_length=32, unique=True, db_index=True)
    name_fr = models.CharField(_("commercial name (French)"), max_length=200, db_index=True)
    name_en = models.CharField(_("commercial name (English)"), max_length=200, db_index=True, blank=True)
    generic_name = models.CharField(_("generic name (INN)"), max_length=200, db_index=True, blank=True)
    brand_name_fr = models.CharField(_("brand name (French)"), max_length=200, blank=True)
    brand_name_en = models.CharField(_("brand name (English)"), max_length=200, blank=True)

    strength_fr = models.CharField(
        _("strength (French)"), max_length=64, blank=True,
        help_text=_("For example: 500 mg, 250 mg/5 ml"),
    )
    strength_en = models.CharField(
        _("strength (English)"), max_length=64, blank=True,
        help_text=_("For example: 500 mg, 250 mg/5 ml"),
    )
    dosage_form = models.CharField(
        _("dosage form"), max_length=20, choices=DosageForm.choices, default=DosageForm.TABLET,
    )
    pack_size = models.CharField(
        _("pack size"), max_length=64, blank=True,
        help_text=_("For example: box of 100 tablets"),
    )

    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="medicines", verbose_name=_("category"),
    )
    manufacturer = models.ForeignKey(
        Manufacturer, on_delete=models.PROTECT, related_name="medicines",
        null=True, blank=True, verbose_name=_("manufacturer"),
    )
    unit_of_measure = models.ForeignKey(
        UnitOfMeasure, on_delete=models.PROTECT, related_name="medicines",
        verbose_name=_("unit of measure"),
    )

    # --- Pricing ----------------------------------------------------------
    # unit_cost is a *reference* cost for margin display and new-product
    # pricing. Actual inventory valuation always uses the batch landed cost,
    # never this field — batches bought at different prices must not be
    # collapsed to a single catalogue figure.
    unit_cost = MoneyField(_("reference unit cost"))
    selling_price = MoneyField(_("selling price"))
    wholesale_price = MoneyField(
        _("wholesale price"),
        help_text=_("Price applied to institutional customers, if different."),
    )
    vat_rate = PercentField(
        _("VAT rate (%)"), default=Decimal("18"),
        help_text=_("Set to 0 for exempt or zero-rated products."),
    )
    is_vat_exempt = models.BooleanField(
        _("VAT exempt"), default=False,
        help_text=_("Many essential medicines are exempt; this overrides the rate."),
    )

    # --- Stock policy -----------------------------------------------------
    reorder_level = QuantityField(_("reorder level"))
    safety_stock = QuantityField(_("safety stock"))
    max_stock_level = QuantityField(_("maximum stock level"))
    # Distinct from reorder_level: how far ahead expiry alerts should fire for
    # this product. Slow-moving lines need a longer horizon to be sold through.
    expiry_alert_days = models.PositiveIntegerField(_("expiry alert (days)"), default=180)

    # --- Regulatory -------------------------------------------------------
    storage_condition = models.CharField(
        _("storage condition"), max_length=20,
        choices=StorageCondition.choices, default=StorageCondition.AMBIENT,
    )
    storage_notes = models.CharField(_("storage notes"), max_length=255, blank=True)
    requires_prescription = models.BooleanField(_("prescription required"), default=False)
    is_controlled = models.BooleanField(
        _("controlled substance"), default=False,
        help_text=_("Subject to reinforced traceability requirements."),
    )
    atc_code = models.CharField(_("ATC code"), max_length=16, blank=True)
    registration_number = models.CharField(
        _("marketing authorisation number"), max_length=64, blank=True,
    )

    barcode = models.CharField(_("barcode"), max_length=64, blank=True, db_index=True)
    status = models.CharField(
        _("status"), max_length=16, choices=ProductStatus.choices,
        default=ProductStatus.ACTIVE, db_index=True,
    )
    notes_fr = models.TextField(_("notes (French)"), blank=True)
    notes_en = models.TextField(_("notes (English)"), blank=True)

    # Capturing batch and expiry on product entry
    batch_number = models.CharField(_("batch number"), max_length=64, blank=True)
    expiry_date = models.DateField(_("expiry date"), null=True, blank=True)

    class Meta:
        verbose_name = _("medicine")
        verbose_name_plural = _("medicines")
        ordering = ("name_fr",)
        constraints = [
            models.CheckConstraint(
                condition=Q(selling_price__gte=Decimal("0")),
                name="medicine_selling_price_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(unit_cost__gte=Decimal("0")),
                name="medicine_unit_cost_non_negative",
            ),
            # Prevents a data-entry slip from making max < reorder, which would
            # make the replenishment suggestion nonsensical.
            models.CheckConstraint(
                condition=Q(max_stock_level=Decimal("0"))
                | Q(max_stock_level__gte=F("reorder_level")),
                name="medicine_max_stock_above_reorder",
            ),
            models.UniqueConstraint(
                fields=["barcode"],
                condition=Q(deleted_at__isnull=True) & ~Q(barcode=""),
                name="unique_active_barcode",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "name_fr"], name="idx_medicine_status_name_fr"),
            models.Index(fields=["category", "status"], name="idx_medicine_category"),
            models.Index(fields=["generic_name"], name="idx_medicine_generic"),
        ]

    def save(self, *args, **kwargs):
        from apps.core.translation import translate_text
        if self.name_fr and not self.name_en:
            self.name_en = translate_text(self.name_fr, "en")
        elif self.name_en and not self.name_fr:
            self.name_fr = translate_text(self.name_en, "fr")

        if self.brand_name_fr and not self.brand_name_en:
            self.brand_name_en = translate_text(self.brand_name_fr, "en")
        elif self.brand_name_en and not self.brand_name_fr:
            self.brand_name_fr = translate_text(self.brand_name_en, "fr")

        if self.notes_fr and not self.notes_en:
            self.notes_en = translate_text(self.notes_fr, "en")
        elif self.notes_en and not self.notes_fr:
            self.notes_fr = translate_text(self.notes_en, "fr")

        if self.strength_fr and not self.strength_en:
            self.strength_en = translate_text(self.strength_fr, "en")
        elif self.strength_en and not self.strength_fr:
            self.strength_fr = translate_text(self.strength_en, "fr")

        super().save(*args, **kwargs)

    @property
    def name(self) -> str:
        return _localised(self, "name")

    @property
    def brand_name(self) -> str:
        return _localised(self, "brand_name")

    @property
    def notes(self) -> str:
        return _localised(self, "notes")

    @property
    def strength(self) -> str:
        return _localised(self, "strength")

    def __str__(self) -> str:
        parts = [self.name]
        if self.strength:
            parts.append(self.strength)
        return " ".join(parts)

    @property
    def display_name(self) -> str:
        """Name as it should appear on an invoice line."""
        bits = [self.name]
        if self.strength:
            bits.append(self.strength)
        if self.dosage_form:
            bits.append(str(self.get_dosage_form_display()))
        return " · ".join(bits)

    @property
    def effective_vat_rate(self) -> Decimal:
        return Decimal("0") if self.is_vat_exempt else self.vat_rate

    @property
    def requires_cold_chain(self) -> bool:
        return self.storage_condition in {
            StorageCondition.REFRIGERATED,
            StorageCondition.FROZEN,
        }

    @property
    def margin_percent(self) -> Decimal:
        """Reference margin. Real margin is computed per sale from batch cost."""
        if not self.unit_cost:
            return Decimal("0")
        return ((self.selling_price - self.unit_cost) / self.unit_cost) * Decimal("100")

    def price_for(self, customer=None) -> Decimal:
        """
        Resolve the applicable unit price.

        Institutional customers get the wholesale price when one is set;
        otherwise everyone falls back to the standard selling price. Kept as a
        method so tiered pricing can extend here without touching call sites.
        """
        if customer is not None and self.wholesale_price > 0:
            if getattr(customer, "is_institutional", False):
                return self.wholesale_price
        return self.selling_price


class PriceHistory(models.Model):
    """
    Immutable record of every price change.

    Required for compliance: an auditor reconciling an old invoice needs the
    price that was in force on that date, not today's. Append-only for the
    same reason the stock ledger is.
    """

    medicine = models.ForeignKey(
        Medicine, on_delete=models.CASCADE, related_name="price_history",
        verbose_name=_("medicine"),
    )
    old_unit_cost = MoneyField(_("previous cost"))
    new_unit_cost = MoneyField(_("new cost"))
    old_selling_price = MoneyField(_("previous selling price"))
    new_selling_price = MoneyField(_("new selling price"))
    reason = models.CharField(_("reason"), max_length=255, blank=True)
    changed_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, null=True, blank=True,
        related_name="price_changes", verbose_name=_("changed by"),
    )
    effective_from = models.DateTimeField(_("effective from"), default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("price history entry")
        verbose_name_plural = _("price history")
        ordering = ("-effective_from",)
        indexes = [models.Index(fields=["medicine", "-effective_from"])]

    def __str__(self) -> str:
        return f"{self.medicine.product_code}: {self.old_selling_price} → {self.new_selling_price}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Price history entries are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Price history entries cannot be deleted.")
