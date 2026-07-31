"""Password policy validators."""

from __future__ import annotations

import re

from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class ComplexityValidator:
    """
    Requires a mix of character classes.

    Composition rules are a weaker control than length (NIST now de-emphasises
    them), which is why MinimumLengthValidator is set to 12 rather than the
    Django default of 8. This validator is retained because pharmaceutical and
    financial audit checklists in the region still explicitly test for it.
    """

    def __init__(self, min_upper=1, min_lower=1, min_digits=1, min_symbols=1):
        self.min_upper = min_upper
        self.min_lower = min_lower
        self.min_digits = min_digits
        self.min_symbols = min_symbols

    def validate(self, password, user=None):
        errors = []
        if len(re.findall(r"[A-Z]", password)) < self.min_upper:
            errors.append(_("at least %(n)d uppercase letter") % {"n": self.min_upper})
        if len(re.findall(r"[a-z]", password)) < self.min_lower:
            errors.append(_("at least %(n)d lowercase letter") % {"n": self.min_lower})
        if len(re.findall(r"[0-9]", password)) < self.min_digits:
            errors.append(_("at least %(n)d digit") % {"n": self.min_digits})
        if len(re.findall(r"[^A-Za-z0-9]", password)) < self.min_symbols:
            errors.append(_("at least %(n)d special character") % {"n": self.min_symbols})

        if errors:
            raise ValidationError(
                _("The password must contain %(requirements)s.")
                % {"requirements": ", ".join(str(e) for e in errors)},
                code="password_not_complex",
            )

    def get_help_text(self):
        return _(
            "The password must contain at least one uppercase letter, one lowercase "
            "letter, one digit and one special character."
        )


class PasswordHistoryValidator:
    """Blocks reuse of recent passwords."""

    def __init__(self, history_length: int = 5):
        self.history_length = history_length

    def validate(self, password, user=None):
        if user is None or not getattr(user, "pk", None):
            return

        # The password currently in force is checked separately: it is only
        # copied into PasswordHistory *after* validation succeeds, so at this
        # point the history table does not yet contain it. Without this, the
        # one password a user is most likely to retype — the one they are
        # already using — would be the only one the policy failed to block.
        # That matters most right after an administrator reset, where the
        # user could satisfy must_change_password by re-entering the
        # temporary password the administrator already knows.
        if user.password and check_password(password, user.password):
            raise ValidationError(
                _("This is your current password. Choose a different one."),
                code="password_reused",
            )

        recent = user.password_history.all()[: self.history_length]
        for entry in recent:
            if check_password(password, entry.password_hash):
                raise ValidationError(
                    _("This password was used recently. Choose a password you have not used in your last %(n)d passwords.")
                    % {"n": self.history_length},
                    code="password_reused",
                )

    def get_help_text(self):
        return _("You cannot reuse any of your last %(n)d passwords.") % {
            "n": self.history_length
        }
