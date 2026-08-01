"""
The fiscal signature.

Every declared document is identified to the OBR by a signature assembled
from four parts::

    <taxpayer NIF>/<system identifier>/<YYYY-MM-DD>/<document number>

The system identifier is issued by the OBR when the software is certified;
it distinguishes this installation from every other one filing under the
same NIF.

Two properties matter and are the reason this lives in its own module:

  * It is computed locally, with no network call. A document is therefore
    fully identified at the instant it is posted, which is what allows
    declaration to be deferred without leaving a window where an issued
    document has no fiscal identity.

  * It is derived purely from data already frozen on the document, so
    recomputing it later yields the same string. That makes it verifiable
    during an inspection rather than merely stored.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext as _

from apps.core.exceptions import BusinessRuleViolation

SEPARATOR = "/"
DATE_FORMAT = "%Y-%m-%d"


@dataclass(frozen=True)
class FiscalSignature:
    """A parsed signature. `str()` returns the wire form."""

    taxpayer_nif: str
    system_identifier: str
    document_date: str
    document_number: str

    def __str__(self) -> str:
        return SEPARATOR.join(
            (
                self.taxpayer_nif,
                self.system_identifier,
                self.document_date,
                self.document_number,
            )
        )


def build_signature(*, taxpayer_nif: str, system_identifier: str, document_date, document_number: str) -> str:
    """
    Assemble the signature for a document.

    Raises rather than returning a partial string: a signature missing its
    NIF or system identifier would be accepted locally and then rejected by
    the OBR much later, by which time the document has already reached the
    customer. Failing at posting time keeps the error where it can still be
    corrected.
    """
    nif = (taxpayer_nif or "").strip()
    identifier = (system_identifier or "").strip()

    if not nif:
        raise BusinessRuleViolation(
            _("The company NIF is not configured; a fiscal signature cannot be built."),
            code="fiscal_nif_missing",
        )
    if not identifier:
        raise BusinessRuleViolation(
            _(
                "The OBR system identifier is not configured. It is issued when the "
                "software is certified and is required before documents can be declared."
            ),
            code="fiscal_system_id_missing",
        )
    if not document_number:
        raise BusinessRuleViolation(
            _("A document number is required to build a fiscal signature."),
            code="fiscal_number_missing",
        )

    # Accept a date, a datetime or an already-formatted string. Datetimes are
    # reduced to their date: the signature identifies a document, not a moment,
    # and including a time would make it depend on the server's timezone.
    if hasattr(document_date, "strftime"):
        formatted_date = document_date.strftime(DATE_FORMAT)
    else:
        formatted_date = str(document_date)[:10]

    return str(
        FiscalSignature(
            taxpayer_nif=nif,
            system_identifier=identifier,
            document_date=formatted_date,
            document_number=str(document_number),
        )
    )


def parse_signature(value: str) -> FiscalSignature:
    """
    Split a signature back into its parts.

    Only the first three separators are treated as structural, because a
    document number may itself contain one. Splitting from the left with a
    bounded count keeps such numbers intact.
    """
    parts = (value or "").split(SEPARATOR, 3)
    if len(parts) != 4:
        raise ValueError(f"Malformed fiscal signature: {value!r}")
    return FiscalSignature(*parts)


def signature_for_invoice(invoice, *, system_identifier: str, taxpayer_nif: str) -> str:
    """Build the signature for an Invoice from its frozen fields."""
    return build_signature(
        taxpayer_nif=taxpayer_nif,
        system_identifier=system_identifier,
        document_date=invoice.invoice_date,
        document_number=invoice.invoice_number,
    )
