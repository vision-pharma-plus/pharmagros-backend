"""
Invoice PDF rendering.

Includes a French/English number-to-words converter because OHADA commercial
practice expects the total spelled out on an invoice — it is the traditional
guard against a digit being altered after issue.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation

from apps.core.money import format_bif, q_document

# --- French number words ---------------------------------------------------
# French numerals below 100 are irregular (70 = soixante-dix, 80 = quatre-vingts,
# 90 = quatre-vingt-dix), so they are enumerated rather than derived.
_FR_UNITS = [
    "zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit",
    "neuf", "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
    "dix-sept", "dix-huit", "dix-neuf",
]
_FR_TENS = {
    20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante",
    60: "soixante", 80: "quatre-vingt",
}

_EN_UNITS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_EN_TENS = {
    20: "twenty", 30: "thirty", 40: "forty", 50: "fifty",
    60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety",
}


def _fr_below_100(n: int) -> str:
    if n < 20:
        return _FR_UNITS[n]
    if n < 70:
        tens, unit = (n // 10) * 10, n % 10
        if unit == 1 and tens != 80:
            return f"{_FR_TENS[tens]}-et-un"
        return _FR_TENS[tens] if unit == 0 else f"{_FR_TENS[tens]}-{_FR_UNITS[unit]}"
    if n < 80:
        # 70-79 built as soixante + 10..19
        rest = n - 60
        if rest == 11:
            return "soixante-et-onze"
        return f"soixante-{_FR_UNITS[rest]}"
    # 80-99 built as quatre-vingt + 0..19
    rest = n - 80
    if rest == 0:
        return "quatre-vingts"
    return f"quatre-vingt-{_FR_UNITS[rest]}"


def _fr_below_1000(n: int) -> str:
    if n < 100:
        return _fr_below_100(n)
    hundreds, rest = n // 100, n % 100
    if hundreds == 1:
        prefix = "cent"
    else:
        # 'cents' takes an s only when it ends the number.
        prefix = f"{_FR_UNITS[hundreds]} cent" + ("s" if rest == 0 else "")
    return prefix if rest == 0 else f"{prefix} {_fr_below_100(rest)}"


def _en_below_100(n: int) -> str:
    if n < 20:
        return _EN_UNITS[n]
    tens, unit = (n // 10) * 10, n % 10
    return _EN_TENS[tens] if unit == 0 else f"{_EN_TENS[tens]}-{_EN_UNITS[unit]}"


def _en_below_1000(n: int) -> str:
    if n < 100:
        return _en_below_100(n)
    hundreds, rest = n // 100, n % 100
    prefix = f"{_EN_UNITS[hundreds]} hundred"
    return prefix if rest == 0 else f"{prefix} {_en_below_100(rest)}"


def number_to_words(value, language: str = "fr") -> str:
    """
    Spell out a whole-franc amount.

    Handles values up to 999,999,999,999 — comfortably above any realistic
    single invoice, and it degrades to digits beyond that rather than
    producing something wrong.
    """
    amount = int(q_document(value))
    if amount < 0:
        prefix = "moins " if language.startswith("fr") else "minus "
        return prefix + number_to_words(-amount, language)

    french = language.startswith("fr")
    below_1000 = _fr_below_1000 if french else _en_below_1000

    if amount == 0:
        return (_FR_UNITS if french else _EN_UNITS)[0]
    if amount >= 10**12:
        return f"{amount:,}".replace(",", " ")

    scales_fr = [(10**9, "milliard"), (10**6, "million"), (1000, "mille")]
    scales_en = [(10**9, "billion"), (10**6, "million"), (1000, "thousand")]
    scales = scales_fr if french else scales_en

    parts: list[str] = []
    remainder = amount

    for factor, name in scales:
        count = remainder // factor
        if not count:
            continue
        remainder %= factor

        if french and name == "mille":
            # 'mille' is invariable and 'un mille' is not said.
            parts.append("mille" if count == 1 else f"{below_1000(count)} mille")
        elif french:
            # 'millions'/'milliards' do take a plural s.
            plural = "s" if count > 1 else ""
            parts.append(f"{below_1000(count)} {name}{plural}")
        else:
            parts.append(f"{below_1000(count)} {name}")

    if remainder:
        parts.append(below_1000(remainder))

    return " ".join(parts)


def amount_in_words(value, language: str = "fr") -> str:
    """
    Full legal phrasing, e.g. 'Un million sept cent soixante mille francs burundais'.

    French grammar note: a number ending in a bare plural 'millions' or
    'milliards' takes 'de' before the currency — 'quarante-cinq millions DE
    francs burundais' — whereas one followed by a smaller component does not
    ('un million sept cent mille francs'). Getting this wrong is immediately
    visible to a francophone reader on a legal document.
    """
    words = number_to_words(value, language)

    if not language.startswith("fr"):
        text = f"{words} Burundian francs"
        return text[0].upper() + text[1:]

    connector = " de " if words.endswith(("millions", "milliards", "million", "milliard")) else " "
    text = f"{words}{connector}francs burundais"
    return text[0].upper() + text[1:]


# --- OBR tax classes -------------------------------------------------------
# Burundi's revenue authority groups lines by tax class on the invoice
# footer: A is exempt, B the standard 18% rate, C zero-rated.
TAX_CLASS_EXEMPT = "A"
TAX_CLASS_STANDARD = "B"
TAX_CLASS_ZERO = "C"

STANDARD_VAT_RATE = Decimal("18")


def _tax_class(rate) -> str:
    """Map a line's VAT rate onto its OBR tax class letter."""
    rate = Decimal(rate or 0)
    if rate == 0:
        return TAX_CLASS_EXEMPT
    if rate == STANDARD_VAT_RATE:
        return TAX_CLASS_STANDARD
    return TAX_CLASS_ZERO


def _tax_summary(invoice) -> dict:
    """
    Total the invoice by tax class for the OBR summary band.

    Bases are accumulated per class so the printed band adds up to the
    invoice total — a mismatch there is what an inspection looks for.
    """
    bases = {TAX_CLASS_EXEMPT: Decimal(0), TAX_CLASS_STANDARD: Decimal(0), TAX_CLASS_ZERO: Decimal(0)}
    tax_b = Decimal(0)

    for line in invoice.lines.all():
        cls = _tax_class(line.tax_rate)
        bases[cls] += Decimal(line.line_subtotal or 0) - Decimal(line.discount_amount or 0)
        if cls == TAX_CLASS_STANDARD:
            tax_b += Decimal(line.tax_amount or 0)

    return {
        "base_a": format_bif(bases[TAX_CLASS_EXEMPT]),
        "base_b": format_bif(bases[TAX_CLASS_STANDARD]),
        "base_c": format_bif(bases[TAX_CLASS_ZERO]),
        "tax_b": format_bif(tax_b),
        "tax_total": format_bif(invoice.tax_amount),
        "total": format_bif(invoice.total_amount),
    }


def render_invoice_pdf(invoice, *, language: str | None = None) -> bytes:
    """
    Render an invoice to PDF bytes.

    Language defaults to the customer's own, so a francophone pharmacy always
    receives a French document regardless of which staff member printed it.

    Every copy renders identically. Reprints are still counted and audited
    (see `services.register_print`), but they are not marked on the page.
    """
    language = language or "fr"

    with translation.override(language):
        lines = []
        for line in invoice.lines.all():
            # Unit price including tax, so the customer can reconcile the line
            # total against the price they were quoted at the counter.
            # Both operands are coerced to Decimal first. A zero-rated line
            # is the trap: `(0 or 0) / 100` is a float, and Decimal * float
            # raises TypeError — so exempt lines crashed the render.
            unit_price = Decimal(line.unit_price or 0)
            tax_rate = Decimal(line.tax_rate or 0)
            unit_price_ttc = unit_price * (1 + tax_rate / 100)

            lines.append(
                {
                    "line_number": line.line_number,
                    "product_code": line.product_code,
                    "description": line.description,
                    "batch_numbers": line.batch_numbers,
                    "expiry_dates": line.expiry_dates,
                    "quantity": line.quantity,
                    "unit_of_measure": line.unit_of_measure,
                    "discount_percent": line.discount_percent,
                    "tax_rate": line.tax_rate,
                    # The TAX column prints the OBR class letter (A/B/C), not
                    # a rate or an amount: it is what ties each line to the
                    # matching TOTAL A/B/C column in the summary band below,
                    # which is the reconciliation an inspection performs.
                    "tax_class": _tax_class(line.tax_rate),
                    "unit_price_display": format_bif(line.unit_price),
                    "unit_price_ttc_display": format_bif(unit_price_ttc),
                    "line_total_display": format_bif(line.line_total),
                }
            )

        tax_summary = _tax_summary(invoice)

        # The customer block is snapshotted onto the invoice at posting time,
        # but sector lives on the customer record, so it is read live.
        customer = invoice.customer
        customer_sector = customer.get_customer_type_display() if customer else ""

        # The masthead names whoever issued the document. `created_by` is
        # nullable — an invoice raised by a scheduled job has no user — so
        # the row is omitted rather than printed blank.
        issuer = invoice.created_by
        cashier = (issuer.get_full_name() or issuer.email) if issuer else ""

        context = {
            "invoice": invoice,
            "lines": lines,
            "company": settings.COMPANY,
            "customer_sector": customer_sector,
            "cashier": cashier,
            "LANGUAGE_CODE": language,
            "totals": {
                "subtotal": format_bif(invoice.subtotal),
                "discount": format_bif(invoice.discount_amount),
                "taxable": format_bif(invoice.taxable_amount),
                "tax": format_bif(invoice.tax_amount),
                "total": format_bif(invoice.total_amount),
                "paid": format_bif(invoice.paid_amount),
                "due": format_bif(invoice.balance_due),
            },
            "amount_in_words": amount_in_words(invoice.total_amount, language),
            "tax_summary": tax_summary,
        }

        html = render_to_string("pdf/invoice.html", context)

    return _html_to_pdf(html)


class PDFEngineUnavailable(RuntimeError):
    """
    WeasyPrint could not be loaded on this host.

    Raised in place of the raw ImportError/OSError so every caller — the
    invoice endpoint, the report exporters, the email task — can recognise
    'the engine is missing' without each one having to know which exception
    types the native loader happens to raise.
    """


ENGINE_UNAVAILABLE_MESSAGE = (
    "PDF rendering is unavailable on this server: WeasyPrint could not be "
    "loaded. Install the backend dependencies (pip install -r "
    "requirements.txt) and, on Windows, the GTK runtime that provides its "
    "Pango/Cairo libraries."
)


def _html_to_pdf(html: str) -> bytes:
    """
    Convert rendered HTML to PDF bytes.

    Uses WeasyPrint, which implements enough of CSS 2.1 and Paged Media that
    the templates can be written as ordinary stylesheets — flexbox, real
    `@page` margin boxes, `position: fixed` running elements — instead of the
    nested-table-with-absolute-widths style the previous engine forced.

    WeasyPrint needs native Pango/Cairo libraries. They are packaged on Linux
    (see the Dockerfile) but must be installed separately on Windows via the
    GTK runtime. Without them `import weasyprint` raises OSError from deep
    inside cffi's dlopen — at import time, not at render time — so the import
    is wrapped here rather than at module scope: importing this module must
    stay safe on a host that cannot render, otherwise the number-to-words
    helpers and the invoicing app itself become unimportable.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise PDFEngineUnavailable(ENGINE_UNAVAILABLE_MESSAGE) from exc

    # base_url anchors relative asset paths (the logo) to the project root, so
    # `static/logo.png` in a template resolves the same way regardless of the
    # process working directory.
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
