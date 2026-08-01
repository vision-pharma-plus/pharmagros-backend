from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.core.fields import MoneySerializerField
from apps.inventory.models import Warehouse
from apps.partners.models import Supplier

from .models import Category, Manufacturer, Medicine, PriceHistory, UnitOfMeasure


class CategorySerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True, default="")

    class Meta:
        model = Category
        fields = [
            "id", "code", "name", "name_fr", "name_en", "parent", "parent_name",
            "description", "description_fr", "description_en", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ManufacturerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Manufacturer
        fields = [
            "id", "code", "name", "country", "contact_email", "contact_phone",
            "website", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)

    class Meta:
        model = UnitOfMeasure
        fields = [
            "id", "code", "name", "name_fr", "name_en", "base_unit",
            "units_per_pack", "is_active",
        ]
        read_only_fields = ["id"]


class MedicineListSerializer(serializers.ModelSerializer):
    """
    Slim representation for list endpoints.

    A separate serializer from the detail view because listing screens fetch
    hundreds of rows; sending the full regulatory payload for each would
    dominate response time for data the table never displays.
    """

    category_name = serializers.CharField(source="category.name", read_only=True)
    manufacturer_name = serializers.CharField(
        source="manufacturer.name", read_only=True, default="",
    )
    unit_code = serializers.CharField(source="unit_of_measure.code", read_only=True)
    selling_price = MoneySerializerField(read_only=True)
    # The catalogue reference cost, exposed so a purchase order line can offer
    # it as a starting point for the supplier's quoted price. It is a default
    # to be overwritten, never the cost the order is placed at — that is the
    # buyer's to enter, and only the received batch cost values stock.
    unit_cost = MoneySerializerField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    # Included so the point-of-sale screen can preview a line total that
    # matches what the server will store. Without it the client had to assume
    # a flat rate, which was wrong for every VAT-exempt product. It is a model
    # property over already-loaded columns, so it costs no extra queries.
    effective_vat_rate = serializers.DecimalField(
        max_digits=7, decimal_places=4, read_only=True,
    )

    class Meta:
        model = Medicine
        fields = [
            "id", "product_code", "name", "name_fr", "name_en", "generic_name", "display_name",
            "strength", "strength_fr", "strength_en",
            "dosage_form", "category", "category_name", "manufacturer_name",
            "unit_code", "selling_price", "unit_cost", "status", "requires_prescription",
            "is_controlled", "barcode", "effective_vat_rate", "batch_number", "expiry_date",
        ]


class MedicineDetailSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    manufacturer_name = serializers.CharField(
        source="manufacturer.name", read_only=True, default="",
    )
    display_name = serializers.CharField(read_only=True)

    # Unsuffixed aliases for the bilingual pairs. On read they render the
    # model property (the language-appropriate variant); on write they stand
    # in for the French column, which is the authoritative one — the English
    # side is filled by the translation hook in Medicine.save(). Declaring
    # them writable is what lets a client post `name` instead of `name_fr`;
    # left to ModelSerializer they resolve to the read-only property and the
    # submitted value is silently dropped.
    name = serializers.CharField(max_length=200, required=False)
    brand_name = serializers.CharField(
        max_length=200, required=False, allow_blank=True,
    )
    strength = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    effective_vat_rate = serializers.DecimalField(
        max_digits=7, decimal_places=4, read_only=True,
    )
    requires_cold_chain = serializers.BooleanField(read_only=True)

    unit_cost = MoneySerializerField()
    selling_price = MoneySerializerField()
    wholesale_price = MoneySerializerField(required=False)

    # Price changes must carry a reason and are routed through the service
    # layer, so they are accepted here as a write-only field rather than
    # applied directly by the serializer.
    price_change_reason = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
    )

    # Allocated by the numbering service when omitted, so it must not be
    # required on input — but it stays writable so an existing catalogue can
    # be imported with its established codes intact.
    product_code = serializers.CharField(max_length=32, required=False)

    # Opening batch. `batch_number`/`expiry_date` are model columns; these
    # three describe the stock movement that opens the batch and belong to
    # inventory, so they are write-only and consumed by the service layer.
    opening_quantity = serializers.DecimalField(
        max_digits=18, decimal_places=3, write_only=True,
        required=False, allow_null=True, min_value=Decimal("0"),
    )
    opening_supplier = serializers.PrimaryKeyRelatedField(
        queryset=Supplier.objects.all(), write_only=True,
        required=False, allow_null=True,
    )
    opening_warehouse = serializers.PrimaryKeyRelatedField(
        queryset=Warehouse.objects.filter(is_active=True), write_only=True,
        required=False, allow_null=True,
    )

    class Meta:
        model = Medicine
        fields = [
            "id", "product_code", "name", "name_fr", "name_en", "generic_name",
            "brand_name", "brand_name_fr", "brand_name_en", "display_name",
            "strength", "strength_fr", "strength_en", "dosage_form", "pack_size",
            "category", "category_name", "manufacturer", "manufacturer_name",
            "unit_of_measure",
            "unit_cost", "selling_price", "wholesale_price", "vat_rate",
            "is_vat_exempt", "effective_vat_rate",
            "reorder_level", "safety_stock", "max_stock_level", "expiry_alert_days",
            "storage_condition", "storage_notes", "requires_cold_chain",
            "requires_prescription", "is_controlled", "atc_code", "registration_number",
            "barcode", "status", "notes", "notes_fr", "notes_en",
            "batch_number", "expiry_date",
            "opening_quantity", "opening_supplier", "opening_warehouse",
            "price_change_reason",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `name_fr` is non-blank on the model, but a client may legitimately
        # supply either the alias or the suffixed column. Requiredness is
        # therefore enforced across the pair in validate() rather than by the
        # field, which cannot see its counterpart.
        self.fields["name_fr"].required = False

    def validate(self, attrs):
        if "name" in attrs:
            attrs.setdefault("name_fr", attrs.pop("name"))
        else:
            attrs.pop("name", None)
        for alias, column in (
            ("brand_name", "brand_name_fr"),
            ("strength", "strength_fr"),
            ("notes", "notes_fr"),
        ):
            if alias in attrs:
                attrs.setdefault(column, attrs.pop(alias))
            else:
                attrs.pop(alias, None)

        # On create one of the pair must arrive; on update the stored value
        # stands, so absence simply means "unchanged".
        if self.instance is None and not attrs.get("name_fr") and not attrs.get("name_en"):
            raise serializers.ValidationError(
                {"name": ["This field is required."]}
            )

        selling = attrs.get("selling_price", getattr(self.instance, "selling_price", None))
        cost = attrs.get("unit_cost", getattr(self.instance, "unit_cost", None))
        # Selling below cost is legitimate for short-dated clearance, so it is
        # surfaced as a warning in the audit trail rather than rejected here.
        if selling is not None and cost is not None and selling < cost:
            self.context.setdefault("warnings", []).append(
                "Selling price is below unit cost."
            )

        # A batch number and an expiry date are meaningful only together. Half
        # a batch — a lot code with no expiry, or an expiry with no lot —
        # cannot be received, recalled or expired, so it is rejected here
        # rather than stored as unusable data. Only values arriving in this
        # payload are checked: an untouched product keeps its stored lot.
        batch_number = (attrs.get("batch_number") or "").strip()
        expiry_date = attrs.get("expiry_date")

        if batch_number and not expiry_date:
            raise serializers.ValidationError(
                {"expiry_date": _("An expiry date is required when a batch number is given.")}
            )
        if expiry_date and not batch_number:
            raise serializers.ValidationError(
                {"batch_number": _("A batch number is required when an expiry date is given.")}
            )
        if expiry_date and expiry_date < timezone.localdate():
            raise serializers.ValidationError(
                {"expiry_date": _("The expiry date cannot be in the past.")}
            )
        if batch_number:
            attrs["batch_number"] = batch_number

        return attrs


class PriceHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(
        source="changed_by.get_full_name", read_only=True, default="",
    )
    old_selling_price = MoneySerializerField(read_only=True)
    new_selling_price = MoneySerializerField(read_only=True)
    old_unit_cost = MoneySerializerField(read_only=True)
    new_unit_cost = MoneySerializerField(read_only=True)

    class Meta:
        model = PriceHistory
        fields = [
            "id", "medicine", "old_unit_cost", "new_unit_cost",
            "old_selling_price", "new_selling_price", "reason",
            "changed_by", "changed_by_name", "effective_from", "created_at",
        ]
        read_only_fields = fields


class PriceChangeSerializer(serializers.Serializer):
    """Payload for the explicit price-change action."""

    unit_cost = serializers.DecimalField(
        max_digits=18, decimal_places=4, required=False,
    )
    selling_price = serializers.DecimalField(
        max_digits=18, decimal_places=4, required=False,
    )
    reason = serializers.CharField(max_length=255)

    def validate(self, attrs):
        if "unit_cost" not in attrs and "selling_price" not in attrs:
            raise serializers.ValidationError(
                "At least one of unit_cost or selling_price must be supplied."
            )
        return attrs
