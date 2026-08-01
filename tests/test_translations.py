"""
Verify the compiled catalogues actually resolve through Django.

Compiling a .mo proves the file is well-formed; it does not prove Django will
find and use it. This exercises the real `gettext` path with the locale
activated, which is the only thing that confirms a user will see French.
"""

from __future__ import annotations

RESULTS: list[tuple[bool, str, str]] = []


def check(label, got, want):
    ok = got == want
    RESULTS.append((ok, label, f"got={got!r} want={want!r}"))
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got!r}")
    return ok


def check_true(label, value, detail=""):
    return check(label, bool(value), True) if not detail else check(
        f"{label} — {detail}", bool(value), True
    )


def run() -> bool:
    from django.utils import translation

    print("=" * 70)
    print("FRENCH RESOLUTION")
    print("=" * 70)

    with translation.override("fr"):
        from django.utils.translation import gettext as _

        check("model label", _("medicine"), "médicament")
        check("field label", _("expiry date"), "date de péremption")
        check("batch terminology", _("stock batch"), "lot de stock")
        check("invoice terminology", _("invoice"), "facture")
        check("PO terminology", _("purchase order"), "bon de commande")
        check("credit note", _("credit note"), "note de crédit")

        # Business rule messages — the ones a user actually reads when blocked.
        check(
            "insufficient stock",
            _("Insufficient stock available to fulfil this request."),
            "Stock insuffisant pour satisfaire cette demande.",
        )
        check(
            "credit limit",
            _("This sale would exceed the customer's credit limit."),
            "Cette vente dépasserait la limite de crédit du client.",
        )
        check(
            "document locked",
            _("This document has been posted and can no longer be modified."),
            "Ce document a été validé et ne peut plus être modifié.",
        )
        check(
            "separation of duties",
            _(
                "You cannot approve a purchase order that you raised yourself. "
                "Approval must come from a different user."
            ),
            "Vous ne pouvez pas approuver un bon de commande que vous avez vous-même créé. "
            "L'approbation doit provenir d'un autre utilisateur.",
        )
        check(
            "adjustment reason required",
            _("A reason is required for every stock adjustment."),
            "Un motif est obligatoire pour tout ajustement de stock.",
        )

        # Interpolated messages must keep their placeholders intact — a
        # translation that drops %(batch)s would raise at format time.
        template = _(
            "Batch %(batch)s holds %(available)s units; %(requested)s were requested."
        )
        check_true("placeholders preserved", "%(batch)s" in template)
        rendered = template % {
            "batch": "AMX-2026-A",
            "available": "250",
            "requested": "400",
        }
        check_true("interpolation works", "AMX-2026-A" in rendered)
        print(f"        → {rendered}")

        # PDF template strings.
        check("PDF: invoice", _("Invoice"), "Facture")
        check("PDF: billed to", _("Billed to"), "Facturé à")
        check("PDF: cancelled stamp", _("CANCELLED"), "ANNULÉE")
        check("PDF: amount in words", _("Amount in words"), "Arrêté à la somme de")

        # Report column headers used by CSV/Excel export.
        check("report header", _("Product code"), "Code produit")
        check("report header VAT", _("VAT (BIF)"), "TVA (BIF)")
        check("ageing bucket", _("Over 90 days (BIF)"), "Plus de 90 jours (BIF)")

    print()
    print("=" * 70)
    print("ENGLISH FALLS BACK TO SOURCE")
    print("=" * 70)

    with translation.override("en"):
        from django.utils.translation import gettext as _

        # Source strings are English, so English needs no catalogue entries —
        # gettext returns the msgid unchanged. Asserting this explicitly
        # guards against someone "helpfully" adding wrong English entries.
        check("model label", _("medicine"), "medicine")
        check("invoice terminology", _("invoice"), "invoice")
        check(
            "business rule",
            _("Insufficient stock available to fulfil this request."),
            "Insufficient stock available to fulfil this request.",
        )

    print()
    print("=" * 70)
    print("LANGUAGE SWITCHING IS LIVE")
    print("=" * 70)

    from django.utils.translation import gettext as _

    # The same call must yield different text depending on the active locale,
    # with no process restart — this is what makes the header-driven switch work.
    with translation.override("fr"):
        french = _("Insufficient stock available to fulfil this request.")
    with translation.override("en"):
        english = _("Insufficient stock available to fulfil this request.")
    with translation.override("fr"):
        french_again = _("Insufficient stock available to fulfil this request.")

    check_true("fr differs from en", french != english)
    check("fr is stable across switches", french_again, french)
    print(f"        fr: {french}")
    print(f"        en: {english}")

    print()
    print("=" * 70)
    print("CATALOGUE COVERAGE")
    print("=" * 70)

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from extract_messages import extract_messages, read_po  # noqa: E402

    base = Path(__file__).resolve().parent.parent
    messages = extract_messages()
    catalogue = read_po(base / "locale" / "fr" / "LC_MESSAGES" / "django.po")
    translated = sum(1 for key in messages if catalogue.get(key))

    check("all strings translated to French", translated, len(messages))
    print(f"        {translated}/{len(messages)} strings")

    mo_path = base / "locale" / "fr" / "LC_MESSAGES" / "django.mo"
    check_true("compiled .mo exists", mo_path.exists())
    check_true("compiled .mo is non-trivial", mo_path.stat().st_size > 10_000)
    print(f"        {mo_path.name}: {mo_path.stat().st_size:,} bytes")

    print()
    print("=" * 70)
    failed = [entry for entry in RESULTS if not entry[0]]
    print(
        f"TOTAL: {len(RESULTS)} assertions, "
        f"{len(RESULTS) - len(failed)} passed, {len(failed)} failed"
    )
    if failed:
        print("\nFAILURES:")
        for _ok, label, detail in failed:
            print(f"  - {label}: {detail}")
    print("=" * 70)
    return not failed


if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.check")

    import django

    django.setup()

    raise SystemExit(0 if run() else 1)
