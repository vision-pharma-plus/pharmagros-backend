"""Reusable model and serializer fields."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from rest_framework import serializers


class MoneyField(models.DecimalField):
    """
    Monetary column: DECIMAL(18,4), never float.

    Centralised so precision is uniform across every app — a single column
    declared at 2 dp would silently truncate unit costs and desynchronise
    inventory valuation from the general ledger.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", settings.MONEY_MAX_DIGITS)
        kwargs.setdefault("decimal_places", settings.MONEY_DECIMAL_PLACES)
        kwargs.setdefault("default", Decimal("0"))
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # Keep migrations explicit rather than dependent on settings at
        # migration-replay time.
        kwargs["max_digits"] = self.max_digits
        kwargs["decimal_places"] = self.decimal_places
        return name, path, args, kwargs


class QuantityField(models.DecimalField):
    """
    Stock quantity: DECIMAL(18,3).

    Fractional because wholesale units are not always integral — a bulk
    liquid received in litres and issued in millilitres, or a part-carton
    return, both produce non-integer quantities.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 3)
        kwargs.setdefault("default", Decimal("0"))
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["max_digits"] = self.max_digits
        kwargs["decimal_places"] = self.decimal_places
        return name, path, args, kwargs


class PercentField(models.DecimalField):
    """Percentage rate, DECIMAL(7,4) — e.g. 18.0000 for 18% VAT."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 7)
        kwargs.setdefault("decimal_places", 4)
        kwargs.setdefault("default", Decimal("0"))
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["max_digits"] = self.max_digits
        kwargs["decimal_places"] = self.decimal_places
        return name, path, args, kwargs


class MoneySerializerField(serializers.DecimalField):
    """
    Serialises money as a decimal *string*.

    JSON numbers are IEEE-754 doubles in every mainstream JS runtime, so
    emitting 106200.0001 as a number risks the frontend displaying a value
    that differs from the invoice. A string crosses the wire losslessly and
    the frontend formats it with a decimal-safe helper.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", settings.MONEY_MAX_DIGITS)
        kwargs.setdefault("decimal_places", settings.MONEY_DECIMAL_PLACES)
        kwargs.setdefault("coerce_to_string", True)
        super().__init__(*args, **kwargs)
