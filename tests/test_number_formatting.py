"""
Locale-aware number formatting.

Two conventions, and a document must not mix them. French/Burundian groups
thousands with a narrow no-break space and marks decimals with a comma;
English groups with a comma and marks decimals with a point. "1,5" means one
and a half to a French reader and fifteen hundred to an English one, so a
number written in the wrong convention is not a cosmetic problem — on a
dispensing quantity or an invoice total it is read as a different number.

`format_bif` previously accepted a `locale` argument and ignored it, so every
figure printed in French regardless of the document's language.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.money import format_bif, format_number

# U+202F, the narrow no-break space French grouping uses.
NNBSP = " "
# U+00A0, the no-break space before the currency code. A different
# character on purpose: it keeps "590" and "BIF" on the same line, which a
# plain space would not.
NBSP = " "


class TestFrench:
    def test_groups_with_a_narrow_no_break_space(self):
        assert format_number(Decimal("171570"), locale="fr") == f"171{NNBSP}570"

    def test_marks_decimals_with_a_comma(self):
        assert format_number(Decimal("4902.3022"), locale="fr", decimals=4) == (
            f"4{NNBSP}902,3022"
        )

    def test_currency_follows_the_amount(self):
        assert format_bif(Decimal("171570"), locale="fr") == f"171{NNBSP}570{NBSP}BIF"


class TestEnglish:
    def test_groups_with_a_comma(self):
        assert format_number(Decimal("171570"), locale="en") == "171,570"

    def test_marks_decimals_with_a_point(self):
        assert format_number(Decimal("4902.3022"), locale="en", decimals=4) == (
            "4,902.3022"
        )

    def test_currency_follows_the_amount(self):
        assert format_bif(Decimal("171570"), locale="en") == f"171,570{NBSP}BIF"

    def test_comma_grouping_survives_the_decimal_swap(self):
        """
        The English case where a naive two-step replace eats itself.

        Its group separator *is* a comma, so swapping "," then "." without a
        sentinel would reintroduce and then mangle the separators it just set.
        """
        assert format_number(Decimal("1234567.5"), locale="en", decimals=2) == (
            "1,234,567.50"
        )


class TestPrecision:
    def test_decimals_are_printed_not_dropped(self):
        """
        A unit cost must be able to print the decimals it is valued at.

        Forcing whole francs here is what made a valuation line fail its own
        arithmetic: 35 x a displayed 4 902 did not equal the 171 581 beside it.
        """
        assert format_number(Decimal("4902.3022"), locale="fr", decimals=0) == (
            f"4{NNBSP}902"
        )
        assert "3022" in format_number(Decimal("4902.3022"), locale="fr", decimals=4)

    def test_rounds_half_away_from_zero(self):
        """OHADA practice, matching `ROUNDING` — not banker's rounding."""
        assert format_number(Decimal("0.5"), locale="en", decimals=0) == "1"
        assert format_number(Decimal("1.5"), locale="en", decimals=0) == "2"
        assert format_number(Decimal("2.5"), locale="en", decimals=0) == "3"

    def test_pads_to_the_requested_decimals(self):
        assert format_number(Decimal("7"), locale="en", decimals=2) == "7.00"


class TestSigns:
    @pytest.mark.parametrize("locale", ["fr", "en"])
    def test_negative_keeps_its_sign(self, locale):
        assert format_number(Decimal("-2500"), locale=locale).startswith("-")

    def test_zero_is_not_signed(self):
        assert format_number(Decimal("0"), locale="en") == "0"


def test_unknown_locale_falls_back_to_french():
    """The deployment's default, rather than an exception on a bad header."""
    assert format_number(Decimal("1000"), locale="sw") == f"1{NNBSP}000"
