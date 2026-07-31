"""
Gap-free, concurrency-safe document numbering.

Fiscal documents (invoices, credit notes) must carry a sequential number
without gaps or duplicates — tax authorities treat a missing number as a
suppressed sale. Two properties are required:

  * No duplicates under concurrency. Achieved with SELECT ... FOR UPDATE on
    the sequence row, which serialises allocation.
  * No gaps. This is why a Postgres SEQUENCE is deliberately NOT used: a
    sequence increments outside transaction control, so a rolled-back
    transaction permanently burns its number. A locked counter row rolls back
    with the transaction, keeping the series dense.

The cost is that concurrent allocations of the same series serialise briefly.
That is the correct trade for fiscal documents.
"""

from __future__ import annotations

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SequenceScope(models.TextChoices):
    GLOBAL = "GLOBAL", _("Continuous")
    YEARLY = "YEARLY", _("Reset annually")
    MONTHLY = "MONTHLY", _("Reset monthly")


class DocumentSequence(models.Model):
    """
    A named counter, e.g. 'sales.invoice' -> FAC-2026-000148.

    `prefix` supports the placeholders {yyyy}, {yy} and {mm}.
    """

    key = models.CharField(_("key"), max_length=64, unique=True)
    label = models.CharField(_("label"), max_length=128, blank=True)
    prefix = models.CharField(_("prefix"), max_length=32, default="")
    padding = models.PositiveSmallIntegerField(_("padding"), default=6)
    scope = models.CharField(
        _("reset scope"), max_length=16, choices=SequenceScope.choices, default=SequenceScope.YEARLY
    )
    current_value = models.BigIntegerField(_("current value"), default=0)
    period_key = models.CharField(max_length=16, blank=True, editable=False)

    class Meta:
        verbose_name = _("document sequence")
        verbose_name_plural = _("document sequences")
        ordering = ("key",)

    def __str__(self) -> str:
        return f"{self.key} ({self.current_value})"

    def _period_key(self, when) -> str:
        if self.scope == SequenceScope.YEARLY:
            return f"{when:%Y}"
        if self.scope == SequenceScope.MONTHLY:
            return f"{when:%Y-%m}"
        return ""

    def _render(self, value: int, when) -> str:
        prefix = (
            self.prefix.replace("{yyyy}", f"{when:%Y}")
            .replace("{yy}", f"{when:%y}")
            .replace("{mm}", f"{when:%m}")
        )
        return f"{prefix}{value:0{self.padding}d}"


@transaction.atomic
def next_number(key: str, *, when=None) -> str:
    """
    Allocate the next number for `key`.

    MUST be called inside the same transaction as the document insert. If the
    caller's transaction rolls back, the counter rolls back with it and the
    series stays dense.
    """
    when = when or timezone.localtime()

    # select_for_update serialises concurrent allocators on this row.
    sequence = DocumentSequence.objects.select_for_update().filter(key=key).first()
    if sequence is None:
        raise ValueError(
            f"Document sequence '{key}' is not configured. "
            "Run `manage.py seed_sequences` or create it in the admin."
        )

    period = sequence._period_key(when)
    if sequence.period_key != period:
        # Crossed into a new year/month: restart the series.
        sequence.current_value = 0
        sequence.period_key = period

    sequence.current_value += 1
    sequence.save(update_fields=["current_value", "period_key"])
    return sequence._render(sequence.current_value, when)


# Default series, created by the seed command.
DEFAULT_SEQUENCES = [
    {"key": "sales.invoice", "prefix": "FAC-{yyyy}-", "label": "Facture de vente"},
    {"key": "sales.order", "prefix": "CMD-{yyyy}-", "label": "Commande client"},
    {"key": "sales.credit_note", "prefix": "NC-{yyyy}-", "label": "Note de crédit"},
    {"key": "sales.debit_note", "prefix": "ND-{yyyy}-", "label": "Note de débit"},
    {"key": "sales.receipt", "prefix": "REC-{yyyy}-", "label": "Reçu de paiement"},
    {"key": "sales.return", "prefix": "RET-{yyyy}-", "label": "Retour client"},
    {"key": "purchasing.order", "prefix": "BC-{yyyy}-", "label": "Bon de commande"},
    {"key": "purchasing.requisition", "prefix": "DA-{yyyy}-", "label": "Demande d'achat"},
    {"key": "purchasing.receipt", "prefix": "BR-{yyyy}-", "label": "Bon de réception"},
    {"key": "inventory.adjustment", "prefix": "AJ-{yyyy}-", "label": "Ajustement de stock"},
    {"key": "inventory.transfer", "prefix": "TR-{yyyy}-", "label": "Transfert de stock"},
    {"key": "inventory.disposal", "prefix": "DES-{yyyy}-", "label": "Destruction de stock"},
    {"key": "partners.customer", "prefix": "CLI-", "scope": SequenceScope.GLOBAL, "padding": 5,
     "label": "Code client"},
    {"key": "partners.supplier", "prefix": "FRN-", "scope": SequenceScope.GLOBAL, "padding": 5,
     "label": "Code fournisseur"},
    {"key": "catalog.product", "prefix": "MED-", "scope": SequenceScope.GLOBAL, "padding": 6,
     "label": "Code produit"},
]
