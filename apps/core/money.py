"""
Monetary arithmetic for BIF (Burundian Franc).

Two precisions coexist, and conflating them is the classic source of invoices
whose lines do not sum to their stated total:

  * INTERNAL (4 dp) — unit costs, weighted-average costs, tax bases, and any
    intermediate result. A wholesaler buying 10,000 tablets at 12.4567 BIF
    each has a genuinely fractional unit cost; truncating it early loses real
    money at scale.

  * DOCUMENT (0 dp) — what is printed on an invoice, credit note, or payment
    receipt. BIF has no circulating subunit, so anything a customer is asked
    to pay must be a whole franc.

The rule enforced throughout the codebase: compute at INTERNAL precision,
round exactly once at the document boundary, and derive the total from the
rounded line amounts rather than rounding an unrounded total. That guarantees
sum(lines) == total on the printed page, which is what an auditor checks.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

INTERNAL_DP: Final = Decimal("0.0001")
DOCUMENT_DP: Final = Decimal("1")
ZERO: Final = Decimal("0")
HUNDRED: Final = Decimal("100")

# OHADA / Burundian commercial practice rounds half away from zero, not
# banker's rounding. Using ROUND_HALF_EVEN here would produce totals that
# disagree with a customer's hand calculation, which erodes trust even when
# it is statistically defensible.
ROUNDING: Final = ROUND_HALF_UP


def to_decimal(value) -> Decimal:
    """Coerce to Decimal without ever routing through float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # A float has already lost precision; refuse rather than pretend.
        raise TypeError(
            "Refusing to build a monetary Decimal from float — pass a string, "
            "int, or Decimal to preserve precision."
        )
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Not a valid monetary value: {value!r}") from exc


def q_internal(value) -> Decimal:
    """Quantise to internal working precision (4 dp)."""
    return to_decimal(value).quantize(INTERNAL_DP, rounding=ROUNDING)


def q_document(value) -> Decimal:
    """Quantise to printable BIF (whole francs)."""
    return to_decimal(value).quantize(DOCUMENT_DP, rounding=ROUNDING)


def line_subtotal(quantity, unit_price) -> Decimal:
    """Gross line amount before discount and tax, at internal precision."""
    return q_internal(to_decimal(quantity) * to_decimal(unit_price))


def apply_percentage(base, percent) -> Decimal:
    """Return the portion of `base` represented by `percent` (e.g. 18 -> 18%)."""
    return q_internal(to_decimal(base) * to_decimal(percent) / HUNDRED)


def price_ex_tax(price_incl_tax, tax_rate_percent=ZERO) -> Decimal:
    """
    Extract the tax-exclusive (HT) price from a tax-inclusive (TTC) one.

    Catalogue prices are entered and displayed TTC — the shelf price a customer
    actually pays — but every downstream computation (`compute_line`, the tax
    summary, the OBR fiscal payload) needs the HT base. This is the single
    inverse of the tax step in `compute_line`, so the two must stay in sync:

        compute_line:  TTC = HT * (1 + rate/100)
        price_ex_tax:  HT  = TTC / (1 + rate/100)

    A zero rate returns the price unchanged rather than dividing by one, which
    keeps exempt and zero-rated products exact instead of merely close.

    The result is deliberately NOT rounded to whole francs: 180 TTC at 18% is
    152.5424 HT, and quantising here would break the round trip back to 180 on
    the printed line. Rounding stays at the document boundary, as everywhere.
    """
    price = to_decimal(price_incl_tax)
    rate = to_decimal(tax_rate_percent)
    if rate == ZERO:
        return q_internal(price)
    return q_internal(price / (Decimal("1") + rate / HUNDRED))


def compute_line(
    quantity,
    unit_price,
    discount_percent=ZERO,
    tax_rate_percent=ZERO,
) -> dict[str, Decimal]:
    """
    Compute a single document line.

    Order of operations matters and is fixed here so every module agrees:
    discount applies to the gross line, and tax applies to the *discounted*
    net. Taxing before discounting would overcharge VAT, which is a tax
    compliance problem, not merely a rounding preference.

    Returns internal-precision components; the caller rounds for display.
    """
    gross = line_subtotal(quantity, unit_price)
    discount = apply_percentage(gross, discount_percent)
    net = q_internal(gross - discount)
    tax = apply_percentage(net, tax_rate_percent)
    total = q_internal(net + tax)
    return {
        "gross": gross,
        "discount": discount,
        "net": net,
        "tax": tax,
        "total": total,
    }


def sum_money(values) -> Decimal:
    """Sum an iterable of monetary values at internal precision."""
    total = ZERO
    for value in values:
        total += to_decimal(value)
    return q_internal(total)


# Thousands and decimal separators, by locale.
#
# French/Burundian convention groups with a narrow no-break space (U+202F) and
# marks decimals with a comma; English groups with a comma and marks decimals
# with a point. Printed documents keep U+202F — on paper it is unambiguous,
# and it is the convention a Burundian reader expects.
_SEPARATORS = {
    "fr": ("\u202f", ","),
    "en": (",", "."),
}


def format_number(value, *, locale: str = "fr", decimals: int = 0) -> str:
    """
    Render a number with the separators of `locale`.

    The single place separator conventions are applied on the server, so an
    amount, a quantity and a percentage on the same document cannot disagree
    about how a number is written.

    `decimals` is the count actually printed, and it matters beyond cosmetics:
    a unit cost printed to zero decimals while its line value is computed from
    four is exactly how a report comes to show 35 x 4 902 = 171 581.
    """
    group, point = _SEPARATORS.get(locale, _SEPARATORS["fr"])
    decimals = max(decimals, 0)

    amount = to_decimal(value).quantize(
        Decimal(1) if decimals == 0 else Decimal("0." + "0" * decimals),
        rounding=ROUNDING,
    )
    negative = amount < 0
    # Format with the C conventions, then swap in the locale's own, so the
    # grouping logic stays in the standard library. The swap routes through a
    # sentinel because for "en" the target group separator is itself a comma,
    # and a direct two-step replace would then undo its own first step.
    digits = f"{abs(amount):,.{decimals}f}"
    digits = digits.replace(",", "\x00").replace(".", point).replace("\x00", group)
    return f"-{digits}" if negative else digits


def format_bif(value, *, locale: str = "fr") -> str:
    """
    Render a value as BIF for display.

    BIF has no circulating subunit, so amounts print as whole francs. The
    currency code follows the amount in both languages, and the grouping
    follows the locale — an English reader sees "171,570 BIF" where a French
    one sees the same figure grouped their own way.

    The gap before the code is a NO-BREAK SPACE (U+00A0), not a plain one: it
    is what stops a renderer breaking the line between the amount and its
    currency, which would leave a bare number at the end of a line and an
    orphaned "BIF" at the start of the next.
    """
    return f"{format_number(value, locale=locale, decimals=0)}\u00a0BIF"
