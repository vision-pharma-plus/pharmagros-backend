"""Shared validators, including Burundi-specific identifiers."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Burundi NIF (Numéro d'Identification Fiscale), issued by the OBR.
# Commonly presented as four digits, a letter, then four digits — e.g.
# 4000123456 / 1234A5678 depending on issuance era. We normalise to
# uppercase alphanumeric and validate length and charset rather than
# hard-coding a single era's layout, because rejecting a legitimate
# taxpayer's NIF is a worse failure here than accepting a malformed one:
# the field is also verified against OBR records during customer onboarding.
NIF_PATTERN = re.compile(r"^[0-9]{4}[A-Z0-9]{4,8}$")
NIF_MIN_LENGTH = 8
NIF_MAX_LENGTH = 12


def normalise_nif(value: str) -> str:
    """Strip spaces, hyphens and slashes; uppercase."""
    return re.sub(r"[\s\-/.]", "", (value or "")).upper()


def validate_nif(value: str) -> None:
    """
    Validate a Burundian NIF.

    Applied to institutional customers (pharmacies, hospitals, NGOs), for whom
    a NIF is required for a compliant invoice. Left optional at the model level
    so a walk-in cash sale is still possible; the sales service enforces the
    requirement for credit sales and for any invoice above the threshold at
    which the OBR requires taxpayer identification.
    """
    if not value:
        return

    cleaned = normalise_nif(value)

    if not (NIF_MIN_LENGTH <= len(cleaned) <= NIF_MAX_LENGTH):
        raise ValidationError(
            _("The NIF must contain between %(min)d and %(max)d characters."),
            code="nif_length",
            params={"min": NIF_MIN_LENGTH, "max": NIF_MAX_LENGTH},
        )

    if not cleaned.isalnum():
        raise ValidationError(
            _("The NIF must contain only letters and digits."), code="nif_charset"
        )

    if not NIF_PATTERN.match(cleaned):
        raise ValidationError(
            _("The NIF format is invalid. Expected format: 4 digits followed by 4 to 8 alphanumeric characters."),
            code="nif_format",
        )


# International phone numbers, E.164. A number is at most 15 digits including
# the country code (ITU-T E.164) and no national numbering plan uses fewer
# than 7 in international form. Suppliers, manufacturers and institutional
# customers are frequently outside Burundi, so restricting entry to +257 would
# make foreign contacts unrecordable.
#
# Local 8-digit Burundi numbers stay valid and are still normalised to +257,
# because that remains the common case at data entry.
PHONE_PATTERN = re.compile(r"^\+?[1-9][0-9]{6,14}$")
BURUNDI_LOCAL_PATTERN = re.compile(r"^[0-9]{8}$")
BURUNDI_DIALLING_CODE = "257"


def _strip_phone_separators(value: str) -> str:
    """Remove the separators people type: spaces, hyphens, dots, brackets, slashes."""
    return re.sub(r"[\s\-(). /]", "", value or "")


def validate_phone(value: str) -> None:
    """
    Validate an international telephone number.

    Accepts either a bare 8-digit Burundi local number or a full international
    number in E.164 form (optionally with a leading '+'). Separators are
    ignored. The leading digit of a country code is never zero, so a number
    beginning 0 — a national trunk prefix that carries no meaning
    internationally — is rejected in favour of the full country code.
    """
    if not value:
        return

    cleaned = _strip_phone_separators(value)

    if BURUNDI_LOCAL_PATTERN.match(cleaned):
        return

    if not PHONE_PATTERN.match(cleaned):
        raise ValidationError(
            _(
                "Enter a valid telephone number in international format, "
                "for example +257 68 606 080 or +44 20 7946 0958."
            ),
            code="invalid_phone",
        )


# Retained under the original name: model fields and the initial migration
# reference `validate_burundi_phone`, and Django serialises validator paths
# into migration files, so renaming it would break historical migrations.
validate_burundi_phone = validate_phone


def normalise_phone(value: str) -> str:
    """
    Store phone numbers in canonical E.164 form (+ followed by digits).

    A bare 8-digit number is assumed to be Burundian and gains the +257 code;
    anything else is taken to already carry its own country code.
    """
    if not value:
        return ""

    cleaned = _strip_phone_separators(value).lstrip("+")
    if not cleaned:
        return ""

    if BURUNDI_LOCAL_PATTERN.match(cleaned):
        return f"+{BURUNDI_DIALLING_CODE}{cleaned}"

    return f"+{cleaned}"


def validate_upload(uploaded_file) -> None:
    """
    File upload guard.

    Checks extension, declared size, and magic bytes. Extension alone is not
    a control — a .pdf that is actually an HTML document with a script payload
    is a stored-XSS vector when served back to a browser.
    """
    from django.conf import settings

    max_bytes = settings.FILE_UPLOAD_MAX_BYTES
    allowed = set(settings.FILE_UPLOAD_ALLOWED_EXTENSIONS)

    if uploaded_file.size > max_bytes:
        raise ValidationError(
            _("The file exceeds the maximum permitted size of %(mb)d MB."),
            code="file_too_large",
            params={"mb": max_bytes // (1024 * 1024)},
        )

    name = getattr(uploaded_file, "name", "") or ""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in allowed:
        raise ValidationError(
            _("Files of type '.%(ext)s' are not permitted."),
            code="file_type_not_allowed",
            params={"ext": extension},
        )

    signatures = {
        "pdf": [b"%PDF-"],
        "png": [b"\x89PNG\r\n\x1a\n"],
        "jpg": [b"\xff\xd8\xff"],
        "jpeg": [b"\xff\xd8\xff"],
        "xlsx": [b"PK\x03\x04"],
    }
    expected = signatures.get(extension)
    if expected:
        head = uploaded_file.read(8)
        uploaded_file.seek(0)
        if not any(head.startswith(sig) for sig in expected):
            raise ValidationError(
                _("The file content does not match its declared type."),
                code="file_content_mismatch",
            )
