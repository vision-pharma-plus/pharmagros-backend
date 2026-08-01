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


def format_bif(value, *, locale: str = "fr") -> str:
    """
    Render a value as BIF for display.

    French/Burundian convention uses a narrow no-break space as the thousands
    separator and places the currency symbol after the amount. English output
    keeps the same separator for consistency across a bilingual document set —
    a report exported in English must still be reconcilable against the
    French original line by line.
    """
    amount = q_document(value)
    negative = amount < 0
    digits = f"{abs(amount):,.0f}".replace(",", " ")
    rendered = f"{digits} BIF"
    return f"-{rendered}" if negative else rendered
