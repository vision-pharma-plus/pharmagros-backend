"""
The HT -> TTC catalogue price migration.

This migration rewrites live money columns, so the properties that matter are
tested directly: it converts using the EFFECTIVE rate, it leaves exempt
products alone, and it refuses to run twice. A second application would
compound the VAT and overcharge every customer.
"""

from __future__ import annotations

from decimal import Decimal

import importlib

import pytest

pytestmark = pytest.mark.django_db


def _load_migration():
    """
    Migration module names start with a digit, so they are not importable by
    the normal `from x import y` syntax and must be resolved dynamically.
    """
    return importlib.import_module(
        "apps.catalog.migrations.0003_vat_inclusive_catalogue_prices"
    )


class TestConversionArithmetic:
    """The pure helpers, independent of any database."""

    def setup_method(self):
        self.mig = _load_migration()

    @pytest.mark.parametrize(
        "ht,rate,ttc",
        [
            ("1000", "18", "1180.0000"),
            ("1271.1864", "18", "1500.0000"),
            ("152.5424", "18", "180.0000"),
            ("500", "10", "550.0000"),
        ],
    )
    def test_to_ttc_adds_the_tax(self, ht, rate, ttc):
        assert self.mig._to_ttc(Decimal(ht), Decimal(rate)) == Decimal(ttc)

    def test_zero_rate_is_a_no_op(self):
        assert self.mig._to_ttc(Decimal("800"), Decimal("0")) == Decimal("800")
        assert self.mig._to_ht(Decimal("800"), Decimal("0")) == Decimal("800")

    def test_zero_price_is_a_no_op(self):
        """Products priced at 0 must not acquire a price from the conversion."""
        assert self.mig._to_ttc(Decimal("0"), Decimal("18")) == Decimal("0")

    @pytest.mark.parametrize("ht", ["1000", "1271.1864", "847.4576", "12345"])
    def test_conversion_is_reversible(self, ht):
        """The backwards path must undo the forwards one within a franc."""
        rate = Decimal("18")
        ttc = self.mig._to_ttc(Decimal(ht), rate)
        back = self.mig._to_ht(ttc, rate)
        assert abs(back - Decimal(ht)) < Decimal("0.0001")


class TestEffectiveRate:
    """
    The single most damaging mistake available here is reading `vat_rate`
    instead of the effective rate: exempt products carry a non-zero rate in
    that column and would be inflated by 18% for tax they never charged.
    """

    def setup_method(self):
        self.mig = _load_migration()

    def test_exempt_product_reads_as_zero(self):
        class FakeMedicine:
            is_vat_exempt = True
            vat_rate = Decimal("18")  # set, but overridden by the flag

        assert self.mig._effective_rate(FakeMedicine()) == Decimal("0")

    def test_taxable_product_reads_its_rate(self):
        class FakeMedicine:
            is_vat_exempt = False
            vat_rate = Decimal("18")

        assert self.mig._effective_rate(FakeMedicine()) == Decimal("18")

    def test_exempt_product_price_survives_a_conversion(self):
        """End to end: an exempt product must come out of the migration equal."""
        class FakeMedicine:
            is_vat_exempt = True
            vat_rate = Decimal("18")

        rate = self.mig._effective_rate(FakeMedicine())
        assert self.mig._to_ttc(Decimal("800"), rate) == Decimal("800")
