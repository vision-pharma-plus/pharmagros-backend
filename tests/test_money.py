"""
Monetary arithmetic.

Pure unit tests — no database. These guard the property everything financial
depends on: that a figure computed here matches the figure printed on the
invoice, and that neither ever passes through a float.
"""

from decimal import Decimal

import pytest

from apps.core.money import (
    apply_percentage,
    compute_line,
    format_bif,
    line_subtotal,
    q_document,
    q_internal,
    sum_money,
    to_decimal,
)


class TestPrecision:
    def test_float_is_rejected(self):
        """
        A float has already lost precision by the time it reaches us, so
        accepting one would silently launder an error into the ledger.
        """
        with pytest.raises(TypeError, match="float"):
            to_decimal(12.34)

    @pytest.mark.parametrize(
        "value,expected",
        [("12.34", "12.3400"), (100, "100.0000"), (Decimal("0.00005"), "0.0001")],
    )
    def test_internal_precision_is_four_places(self, value, expected):
        assert q_internal(value) == Decimal(expected)

    def test_fractional_unit_cost_survives(self):
        """10,000 tablets at 12.4567 is a real number, not a rounding artefact."""
        assert line_subtotal(Decimal("10000"), Decimal("12.4567")) == Decimal(
            "124567.0000"
        )


class TestRounding:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("0.5", "1"),
            ("1.5", "2"),
            ("2.5", "3"),      # half-up, not banker's rounding
            ("-2.5", "-3"),    # away from zero
            ("1.4", "1"),
            ("1.6", "2"),
        ],
    )
    def test_document_rounding_is_half_up(self, value, expected):
        """
        OHADA practice rounds half away from zero. Banker's rounding would
        produce totals that disagree with a customer's hand calculation.
        """
        assert q_document(value) == Decimal(expected)


class TestLineComputation:
    def test_discount_applies_before_tax(self):
        """
        Taxing before discounting would overcharge VAT — a tax compliance
        problem, not a rounding preference.
        """
        result = compute_line(
            Decimal("100"), Decimal("1000"), Decimal("10"), Decimal("18")
        )
        assert result["gross"] == Decimal("100000.0000")
        assert result["discount"] == Decimal("10000.0000")
        assert result["net"] == Decimal("90000.0000")
        assert result["tax"] == Decimal("16200.0000")   # 18% of 90,000, not 100,000
        assert result["total"] == Decimal("106200.0000")

    def test_zero_tax_yields_no_tax(self):
        result = compute_line(Decimal("10"), Decimal("100"), Decimal("0"), Decimal("0"))
        assert result["tax"] == Decimal("0")
        assert result["total"] == result["net"]

    def test_full_discount_zeroes_the_line(self):
        result = compute_line(
            Decimal("10"), Decimal("100"), Decimal("100"), Decimal("18")
        )
        assert result["net"] == Decimal("0")
        assert result["total"] == Decimal("0")

    def test_apply_percentage(self):
        assert apply_percentage(Decimal("1000"), Decimal("18")) == Decimal("180.0000")


class TestFooting:
    def test_sum_of_rounded_lines_equals_printed_total(self):
        """
        An auditor adds the visible line totals and expects the visible grand
        total. This is the assertion that guarantees it.
        """
        lines = [
            compute_line(Decimal("120"), Decimal("1350"), Decimal("5"), Decimal("18")),
            compute_line(Decimal("35"), Decimal("8400"), Decimal("0"), Decimal("18")),
            compute_line(Decimal("500"), Decimal("47.5"), Decimal("2.5"), Decimal("0")),
        ]
        printed = [q_document(line["total"]) for line in lines]
        assert sum(printed) == Decimal("551678")
        assert q_document(sum_money(line["total"] for line in lines)) == sum(printed)


class TestFormatting:
    # The formatter uses a narrow no-break space as the thousands separator
    # (French/Burundian convention) and a non-breaking space before the
    # currency, so an amount never wraps mid-number. Asserting the literal
    # characters keeps that deliberate — a plain space would be a regression.
    NNBSP = " "
    NBSP = "\xa0"

    @pytest.mark.parametrize(
        "digits,expected_groups",
        [
            ("1770000", ["1", "770", "000"]),
            ("590", ["590"]),
            ("0", ["0"]),
        ],
    )
    def test_bif_formatting(self, digits, expected_groups):
        expected = self.NNBSP.join(expected_groups) + self.NBSP + "BIF"
        assert format_bif(Decimal(digits)) == expected

    def test_negative_amount(self):
        expected = f"-4{self.NNBSP}500{self.NBSP}BIF"
        assert format_bif(Decimal("-4500")) == expected

    def test_separators_are_non_breaking(self):
        """Guards the specific characters rather than 'some kind of space'."""
        rendered = format_bif(Decimal("1770000"))
        assert self.NNBSP in rendered
        assert self.NBSP in rendered
        assert " " not in rendered  # no plain ASCII spaces

    def test_large_value_is_exact(self):
        """A value that a float64 cannot represent must still render exactly."""
        rendered = format_bif(Decimal("9007199254740993"))
        assert rendered.replace(self.NNBSP, "").replace(self.NBSP, " ") == (
            "9007199254740993 BIF"
        )
