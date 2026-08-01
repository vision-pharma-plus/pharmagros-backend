"""
OBR electronic declaration.

Covers the three properties the integration exists to guarantee: a document
acquires a fiscal identity locally at posting time, declaration never blocks
the counter, and a document the OBR refuses is eventually surfaced to a human
rather than retried forever.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import requests
from django.core.cache import cache
from django.utils import timezone

from apps.core.exceptions import BusinessRuleViolation
from apps.invoicing.fiscal import payload as payload_builder
from apps.invoicing.fiscal import service as fiscal
from apps.invoicing.fiscal.client import OBRError
from apps.invoicing.fiscal.signature import build_signature, parse_signature
from apps.invoicing.models import (
    FiscalRequestLog,
    FiscalRequestOutcome,
    FiscalStatus,
    InvoiceType,
)
from apps.invoicing.services import cancel_invoice, post_invoice
from apps.invoicing.tasks import declare_pending_invoices
from apps.sales.models import SaleType
from apps.sales.services import confirm_sale, create_sale

pytestmark = pytest.mark.django_db


OBR_TEST_SETTINGS = {
    "ENABLED": True,
    "BASE_URL": "https://ebms.test.invalid/api",
    "USERNAME": "tester",
    "PASSWORD": "secret",
    "SYSTEM_ID": "SYS-0042",
    "PATHS": {
        "login": "login",
        "add_invoice": "addInvoice",
        "cancel_invoice": "cancelInvoice",
        "check_tin": "checkTIN",
    },
    "CONNECT_TIMEOUT": 1.0,
    "READ_TIMEOUT": 2.0,
    "MAX_ATTEMPTS": 3,
    "START_DATE": "",
    "TAXPAYER": {
        "TYPE": 2,
        "VAT_SUBJECT": True,
        "CONSUMPTION_TAX_SUBJECT": False,
        "WITHHOLDING_TAX_SUBJECT": False,
        "TRADE_REGISTER": "RC/1234",
        "PROVINCE": "BUJUMBURA MAIRIE",
        "COMMUNE": "MUKAZA",
        "QUARTIER": "ROHERO",
        "AVENUE": "DU COMMERCE",
        "RUE": "12",
        "NUMBER": "8",
    },
}


@pytest.fixture
def obr_enabled(settings):
    settings.OBR = OBR_TEST_SETTINGS
    cache.delete("obr:auth_token")
    yield settings
    cache.delete("obr:auth_token")


@pytest.fixture
def obr_disabled(settings):
    settings.OBR = {**OBR_TEST_SETTINGS, "ENABLED": False}
    return settings


@pytest.fixture
def posted_invoice(product, warehouse, credit_customer, batch, pharmacist):
    """A confirmed credit sale with its posted invoice."""
    sale = create_sale(
        customer=credit_customer,
        warehouse=warehouse,
        sale_type=SaleType.CREDIT,
        lines=[{"product": product, "quantity": Decimal("10")}],
        actor=pharmacist,
    )
    _sale, invoice, _receipt = confirm_sale(sale, actor=pharmacist)
    return invoice


class _FakeResponse:
    """Stands in for a requests.Response without touching the network."""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _auth_response():
    # A token whose expiry claim is an hour out. The client reads `exp` to
    # decide how long to cache it, so the claims segment must decode.
    import base64
    import json

    claims = {"exp": int((timezone.now() + timedelta(hours=1)).timestamp())}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token = f"header.{encoded}.signature"
    return _FakeResponse(200, {"result": {"token": token}})


def _accepted_response(number="OBR-77", signature="ELEC-SIG-XYZ"):
    return _FakeResponse(
        200,
        {
            "result": {
                "invoice_registered_number": number,
                "invoice_registered_date": "2026-08-01 10:30:00",
                "electronic_signature": signature,
            }
        },
    )


class TestSignature:
    def test_has_four_slash_separated_parts(self):
        signature = build_signature(
            taxpayer_nif="4001902867",
            system_identifier="SYS-0042",
            document_date="2026-08-01",
            document_number="FAC-2026-000148",
        )
        assert signature == "4001902867/SYS-0042/2026-08-01/FAC-2026-000148"

    def test_datetime_is_reduced_to_its_date(self):
        """A signature identifies a document, not a moment."""
        moment = timezone.datetime(2026, 8, 1, 14, 35, 9)
        signature = build_signature(
            taxpayer_nif="400",
            system_identifier="SYS",
            document_date=moment,
            document_number="F-1",
        )
        assert signature == "400/SYS/2026-08-01/F-1"

    def test_missing_system_identifier_is_refused(self):
        with pytest.raises(BusinessRuleViolation) as exc:
            build_signature(
                taxpayer_nif="400",
                system_identifier="",
                document_date="2026-08-01",
                document_number="F-1",
            )
        assert exc.value.code == "fiscal_system_id_missing"

    def test_missing_nif_is_refused(self):
        with pytest.raises(BusinessRuleViolation) as exc:
            build_signature(
                taxpayer_nif="  ",
                system_identifier="SYS",
                document_date="2026-08-01",
                document_number="F-1",
            )
        assert exc.value.code == "fiscal_nif_missing"

    def test_roundtrips_through_parse(self):
        original = "4001902867/SYS-0042/2026-08-01/FAC-2026-000148"
        parsed = parse_signature(original)
        assert parsed.taxpayer_nif == "4001902867"
        assert parsed.document_number == "FAC-2026-000148"
        assert str(parsed) == original

    def test_document_number_containing_a_slash_survives(self):
        """Only the first three separators are structural."""
        parsed = parse_signature("400/SYS/2026-08-01/FAC/2026/148")
        assert parsed.document_number == "FAC/2026/148"

    def test_malformed_signature_raises(self):
        with pytest.raises(ValueError):
            parse_signature("400/SYS")


class TestPostingAssignsIdentity:
    def test_posting_marks_the_invoice_pending(self, obr_enabled, posted_invoice):
        assert posted_invoice.fiscal_status == FiscalStatus.PENDING
        assert posted_invoice.fiscal_signature

    def test_signature_embeds_nif_system_id_and_number(self, obr_enabled, posted_invoice):
        parsed = parse_signature(posted_invoice.fiscal_signature)
        assert parsed.system_identifier == "SYS-0042"
        assert parsed.document_number == posted_invoice.invoice_number

    def test_posting_makes_no_network_call(self, obr_enabled, product, warehouse, credit_customer, batch, pharmacist):
        """
        The counter must not depend on the OBR being reachable.

        Any attempt to use the network during posting fails the test.
        """
        sale = create_sale(
            customer=credit_customer,
            warehouse=warehouse,
            sale_type=SaleType.CREDIT,
            lines=[{"product": product, "quantity": Decimal("5")}],
            actor=pharmacist,
        )
        with patch("requests.post", side_effect=AssertionError("posting hit the network")):
            _sale, invoice, _receipt = confirm_sale(sale, actor=pharmacist)

        assert invoice.fiscal_status == FiscalStatus.PENDING

    def test_disabled_integration_leaves_invoice_undeclarable(self, obr_disabled, posted_invoice):
        assert posted_invoice.fiscal_status == FiscalStatus.NOT_REQUIRED
        assert posted_invoice.fiscal_signature == ""

    def test_proforma_is_never_declarable(self, obr_enabled, posted_invoice):
        posted_invoice.invoice_type = InvoiceType.PROFORMA
        assert fiscal.is_declarable(posted_invoice) is False

    def test_documents_before_go_live_are_skipped(self, obr_enabled, posted_invoice):
        obr_enabled.OBR = {**OBR_TEST_SETTINGS, "START_DATE": "2099-01-01"}
        assert fiscal.is_declarable(posted_invoice) is False


class TestDeclaration:
    def test_successful_declaration_records_obr_reply(self, obr_enabled, posted_invoice):
        with patch("requests.post", side_effect=[_auth_response(), _accepted_response()]):
            fiscal.declare_invoice(posted_invoice)

        posted_invoice.refresh_from_db()
        assert posted_invoice.fiscal_status == FiscalStatus.DECLARED
        assert posted_invoice.obr_registered_number == "OBR-77"
        assert posted_invoice.obr_electronic_signature == "ELEC-SIG-XYZ"
        assert posted_invoice.declared_at is not None
        assert posted_invoice.last_declaration_error == ""

    def test_declaration_is_logged_with_request_and_response(self, obr_enabled, posted_invoice):
        with patch("requests.post", side_effect=[_auth_response(), _accepted_response()]):
            fiscal.declare_invoice(posted_invoice)

        log = FiscalRequestLog.objects.get(invoice=posted_invoice, operation="declare")
        assert log.outcome == FiscalRequestOutcome.SUCCEEDED
        assert log.request_body["invoice_number"] == posted_invoice.invoice_number
        assert log.response_body["result"]["invoice_registered_number"] == "OBR-77"

    def test_declaring_twice_is_a_no_op(self, obr_enabled, posted_invoice):
        with patch("requests.post", side_effect=[_auth_response(), _accepted_response()]):
            fiscal.declare_invoice(posted_invoice)

        # A second pass must not file the document again.
        with patch("requests.post", side_effect=AssertionError("re-declared")):
            fiscal.declare_invoice(posted_invoice)

        posted_invoice.refresh_from_db()
        assert posted_invoice.declaration_attempts == 1

    def test_transport_failure_leaves_it_pending_for_retry(self, obr_enabled, posted_invoice):
        with patch(
            "requests.post",
            side_effect=[_auth_response(), requests.ConnectionError("link down")],
        ):
            with pytest.raises(OBRError):
                fiscal.declare_invoice(posted_invoice)

        posted_invoice.refresh_from_db()
        assert posted_invoice.fiscal_status == FiscalStatus.PENDING
        assert posted_invoice.declaration_attempts == 1
        assert "link down" in posted_invoice.last_declaration_error

    def test_rejection_on_the_merits_is_not_retried(self, obr_enabled, posted_invoice):
        """A 400 means the document is wrong; retrying cannot help."""
        rejection = _FakeResponse(400, {"msg": "invoice_number already filed"})
        with patch("requests.post", side_effect=[_auth_response(), rejection]):
            with pytest.raises(OBRError):
                fiscal.declare_invoice(posted_invoice)

        posted_invoice.refresh_from_db()
        assert posted_invoice.fiscal_status == FiscalStatus.REJECTED
        assert "already filed" in posted_invoice.last_declaration_error

    def test_failed_attempt_is_logged(self, obr_enabled, posted_invoice):
        rejection = _FakeResponse(400, {"msg": "bad payload"})
        with patch("requests.post", side_effect=[_auth_response(), rejection]):
            with pytest.raises(OBRError):
                fiscal.declare_invoice(posted_invoice)

        log = FiscalRequestLog.objects.get(invoice=posted_invoice, operation="declare")
        assert log.outcome == FiscalRequestOutcome.FAILED
        assert log.status_code == 400
        assert "bad payload" in log.error_message

    def test_server_error_exhausts_attempts_then_gives_up(self, obr_enabled, posted_invoice):
        """After MAX_ATTEMPTS a retryable failure stops being retried."""
        posted_invoice.declaration_attempts = OBR_TEST_SETTINGS["MAX_ATTEMPTS"] - 1
        posted_invoice.save(update_fields=["declaration_attempts"])

        outage = _FakeResponse(503, {"msg": "service unavailable"})
        with patch("requests.post", side_effect=[_auth_response(), outage]):
            with pytest.raises(OBRError):
                fiscal.declare_invoice(posted_invoice)

        posted_invoice.refresh_from_db()
        assert posted_invoice.fiscal_status == FiscalStatus.REJECTED
        assert posted_invoice.declaration_exhausted is True

    def test_draft_invoice_cannot_be_declared(self, obr_enabled, cash_customer, product, pharmacist):
        from apps.invoicing.services import create_invoice

        draft = create_invoice(
            customer=cash_customer,
            lines=[
                {
                    "product": product,
                    "description": "X",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("100"),
                }
            ],
            actor=pharmacist,
        )
        with pytest.raises(BusinessRuleViolation) as exc:
            fiscal.declare_invoice(draft)
        assert exc.value.code == "invoice_not_posted"


class TestBackoff:
    def test_delay_grows_with_each_attempt(self):
        first = fiscal.backoff_for(1)
        second = fiscal.backoff_for(2)
        third = fiscal.backoff_for(3)
        assert first < second < third

    def test_delay_is_capped(self):
        assert fiscal.backoff_for(50) == fiscal.BACKOFF_CAP

    def test_never_attempted_document_is_due_immediately(self, obr_enabled, posted_invoice):
        assert fiscal.is_due_for_retry(posted_invoice) is True

    def test_recent_failure_is_not_yet_due(self, obr_enabled, posted_invoice):
        posted_invoice.declaration_attempts = 2
        posted_invoice.save(update_fields=["declaration_attempts"])
        posted_invoice.refresh_from_db()
        assert fiscal.is_due_for_retry(posted_invoice) is False

    def test_old_failure_becomes_due(self, obr_enabled, posted_invoice):
        posted_invoice.declaration_attempts = 1
        posted_invoice.save(update_fields=["declaration_attempts"])
        later = timezone.now() + fiscal.backoff_for(1) + timedelta(minutes=1)
        assert fiscal.is_due_for_retry(posted_invoice, now=later) is True


class TestSweep:
    def test_sweep_declares_queued_documents(self, obr_enabled, posted_invoice):
        with patch("requests.post", side_effect=[_auth_response(), _accepted_response()]):
            result = declare_pending_invoices()

        assert result["declared"] == 1
        posted_invoice.refresh_from_db()
        assert posted_invoice.fiscal_status == FiscalStatus.DECLARED

    def test_sweep_is_inert_when_disabled(self, obr_disabled, posted_invoice):
        with patch("requests.post", side_effect=AssertionError("called while disabled")):
            result = declare_pending_invoices()
        assert result == {"skipped": "disabled"}

    def test_one_failure_does_not_stall_the_queue(
        self, obr_enabled, product, warehouse, credit_customer, batch, pharmacist
    ):
        """Each document is handled independently."""
        invoices = []
        for _ in range(2):
            sale = create_sale(
                customer=credit_customer,
                warehouse=warehouse,
                sale_type=SaleType.CREDIT,
                lines=[{"product": product, "quantity": Decimal("2")}],
                actor=pharmacist,
            )
            invoices.append(confirm_sale(sale, actor=pharmacist)[1])

        responses = [
            _auth_response(),
            _FakeResponse(400, {"msg": "rejected"}),   # first document refused
            _accepted_response(),                      # second still goes through
        ]
        with patch("requests.post", side_effect=responses):
            result = declare_pending_invoices()

        assert result["declared"] == 1
        assert result["failed"] == 1

    def test_sweep_respects_backoff(self, obr_enabled, posted_invoice):
        posted_invoice.declaration_attempts = 2
        posted_invoice.save(update_fields=["declaration_attempts"])

        with patch("requests.post", side_effect=AssertionError("ignored backoff")):
            result = declare_pending_invoices()

        assert result["deferred"] == 1


class TestCancellation:
    def test_cancelling_a_queued_document_drops_it_from_the_queue(
        self, obr_enabled, posted_invoice, pharmacist
    ):
        """Never declared, so there is nothing at the OBR to withdraw."""
        cancel_invoice(posted_invoice, reason="Keyed in error", actor=pharmacist)

        posted_invoice.refresh_from_db()
        assert posted_invoice.fiscal_status == FiscalStatus.NOT_REQUIRED
        assert fiscal.pending_queue().count() == 0

    def test_cancelling_a_declared_document_keeps_it_for_withdrawal(
        self, obr_enabled, posted_invoice, pharmacist
    ):
        with patch("requests.post", side_effect=[_auth_response(), _accepted_response()]):
            fiscal.declare_invoice(posted_invoice)

        cancel_invoice(posted_invoice, reason="Returned in full", actor=pharmacist)

        posted_invoice.refresh_from_db()
        # Still DECLARED: the withdrawal has not yet been filed.
        assert posted_invoice.fiscal_status == FiscalStatus.DECLARED

    def test_withdrawal_references_the_original_signature(
        self, obr_enabled, posted_invoice, pharmacist
    ):
        with patch("requests.post", side_effect=[_auth_response(), _accepted_response()]):
            fiscal.declare_invoice(posted_invoice)
        posted_invoice.refresh_from_db()
        signature = posted_invoice.fiscal_signature

        with patch("requests.post", side_effect=[_FakeResponse(200, {"result": {}})]) as sender:
            fiscal.declare_cancellation(posted_invoice, reason="Returned in full")

        sent_body = sender.call_args.kwargs["json"]
        assert sent_body["invoice_signature"] == signature
        assert sent_body["cn_motif"] == "Returned in full"

        posted_invoice.refresh_from_db()
        assert posted_invoice.fiscal_status == FiscalStatus.CANCELLED

    def test_undeclared_document_cannot_be_withdrawn(self, obr_enabled, posted_invoice):
        with pytest.raises(BusinessRuleViolation) as exc:
            fiscal.declare_cancellation(posted_invoice, reason="x")
        assert exc.value.code == "invoice_not_declared"


class TestPayload:
    def test_totals_match_the_printed_document(self, obr_enabled, posted_invoice):
        body = payload_builder.build_declaration(posted_invoice, signature="sig")

        declared = sum(item["item_price_wvat"] for item in body["invoice_items"])
        assert declared == pytest.approx(float(posted_invoice.total_amount), abs=1.0)

    def test_credit_sale_is_declared_as_credit(self, obr_enabled, posted_invoice):
        body = payload_builder.build_declaration(posted_invoice, signature="sig")
        assert body["payment_type"] == payload_builder.PAYMENT_CODE_CREDIT

    def test_standard_invoice_is_type_fn(self, obr_enabled, posted_invoice):
        body = payload_builder.build_declaration(posted_invoice, signature="sig")
        assert body["invoice_type"] == payload_builder.DOC_TYPE_NORMAL

    def test_taxpayer_address_is_sent_as_separate_divisions(self, obr_enabled, posted_invoice):
        body = payload_builder.build_declaration(posted_invoice, signature="sig")
        assert body["tp_address_commune"] == "MUKAZA"
        assert body["tp_address_quartier"] == "ROHERO"
        assert body["tp_address_province"] == "BUJUMBURA MAIRIE"

    def test_customer_identity_comes_from_the_frozen_snapshot(
        self, obr_enabled, posted_invoice, credit_customer
    ):
        """
        A re-declaration years later must reproduce what was filed, so the
        payload reads the invoice's snapshot rather than the live customer.
        """
        original_name = posted_invoice.customer_name
        credit_customer.business_name = "RENAMED SARL"
        credit_customer.save(update_fields=["business_name"])

        body = payload_builder.build_declaration(posted_invoice, signature="sig")
        assert body["customer_name"] == original_name

    def test_credit_note_references_the_original_signature(
        self, obr_enabled, posted_invoice, product, pharmacist
    ):
        from apps.invoicing.services import issue_credit_note

        with patch("requests.post", side_effect=[_auth_response(), _accepted_response()]):
            fiscal.declare_invoice(posted_invoice)
        posted_invoice.refresh_from_db()

        note = issue_credit_note(
            posted_invoice,
            lines=[
                {
                    "product": product,
                    "description": "Return",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("100"),
                    "tax_rate": Decimal("18"),
                }
            ],
            reason="Damaged on arrival",
            actor=pharmacist,
        )

        body = payload_builder.build_declaration(note, signature="sig")
        assert body["invoice_type"] == payload_builder.DOC_TYPE_CORRECTION
        assert body["invoice_ref"] == posted_invoice.fiscal_signature
        assert body["cn_motif"] == "Damaged on arrival"


class TestPrintCounter:
    def test_first_copy_is_the_original(self, posted_invoice, pharmacist):
        from apps.invoicing.services import register_print

        assert register_print(posted_invoice, actor=pharmacist) == 1

    def test_each_copy_gets_a_distinct_number(self, posted_invoice, pharmacist):
        """
        Two prints must never both believe they are the original — that is
        what would put two unmarked originals into circulation.
        """
        from apps.invoicing.services import register_print

        numbers = {register_print(posted_invoice, actor=pharmacist) for _ in range(4)}
        assert numbers == {1, 2, 3, 4}
