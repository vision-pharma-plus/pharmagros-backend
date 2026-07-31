"""
RBAC, audit integrity, purchasing controls and the API boundary.

The permission tests matter most: they confirm a technician genuinely cannot
reach a privileged endpoint, rather than that we merely intended it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from apps.core.audit import record, verify_chain
from apps.core.exceptions import BusinessRuleViolation, InvalidStateTransition
from apps.core.models import AuditAction, AuditLog

pytestmark = pytest.mark.django_db


class TestRolePermissions:
    def test_administrator_holds_every_permission(self, admin_user):
        from apps.accounts.rbac import ALL_CODES

        assert admin_user.effective_permissions() == set(ALL_CODES)

    def test_auditor_is_read_only(self, auditor):
        """
        An auditor who could alter records would defeat the purpose of the
        role. This asserts it rather than trusting the seed data.
        """
        write_markers = (
            ".add_", ".change_", ".delete_", ".adjust_", ".approve_",
            ".cancel_", ".post_", ".receive_", ".issue_stock", ".transfer_",
            ".dispose_", ".record_", ".process_", ".manage_", ".assign_",
        )
        writes = [
            code
            for code in auditor.effective_permissions()
            if any(marker in code for marker in write_markers)
        ]
        assert writes == []

    def test_pharmacist_inherits_technician(self, pharmacist, technician):
        assert technician.effective_permissions() <= pharmacist.effective_permissions()

    def test_technician_lacks_escalated_permissions(self, technician):
        for code in (
            "sales.apply_discount",
            "sales.sell_on_credit",
            "inventory.adjust_stock",
            "catalog.change_price",
            "purchasing.approve_order",
        ):
            assert not technician.has_perm_code(code), code

    def test_suspended_user_loses_all_permissions(self, admin_user):
        admin_user.is_suspended = True
        admin_user.save(update_fields=["is_suspended"])
        admin_user.invalidate_permission_cache()

        assert admin_user.has_perm_code("catalog.view_medicine") is False


class TestApiAuthorisation:
    def test_anonymous_is_rejected(self, api_client):
        assert api_client.get("/api/v1/catalog/medicines/").status_code == 401

    def test_technician_blocked_from_privileged_endpoints(
        self, auth_client, technician
    ):
        client = auth_client(technician)
        for path in (
            "/api/v1/auth/users/",
            "/api/v1/inventory/valuation/",
        ):
            assert client.get(path).status_code == 403, path

    def test_technician_can_read_catalogue(self, auth_client, technician, product):
        client = auth_client(technician)
        assert client.get("/api/v1/catalog/medicines/").status_code == 200

    def test_auditor_can_read_audit_log(self, auth_client, auditor):
        client = auth_client(auditor)
        assert client.get("/api/v1/core/audit-logs/").status_code == 200

    def test_auditor_cannot_write(self, auth_client, auditor):
        client = auth_client(auditor)
        assert client.post("/api/v1/sales/sales/", {}, format="json").status_code == 403

    def test_audit_log_has_no_write_route(self, auth_client, admin_user):
        """
        The trail must offer no write path at any permission level. DRF
        evaluates permissions before method routing, so either 403 or 405 is
        an acceptable refusal.
        """
        client = auth_client(admin_user)
        response = client.post("/api/v1/core/audit-logs/", {}, format="json")
        assert response.status_code in (403, 405)

    def test_stock_ledger_has_no_write_route(self, auth_client, admin_user):
        client = auth_client(admin_user)
        response = client.post("/api/v1/inventory/movements/", {}, format="json")
        assert response.status_code in (403, 405)


class TestApiEndpoints:
    def test_profile_returns_permissions(self, auth_client, pharmacist):
        client = auth_client(pharmacist)
        response = client.get("/api/v1/auth/me/")
        assert response.status_code == 200
        assert len(response.json()["permissions"]) > 0

    def test_dashboard_hides_margin_without_permission(
        self, auth_client, technician
    ):
        """
        Margin is commercially sensitive. The UI hides it, but the API must
        also strip it — a hidden field is not a control.
        """
        client = auth_client(technician)
        response = client.get("/api/v1/reporting/dashboard/")
        assert response.status_code == 200
        assert "daily_margin" not in response.json()["sales"]

    def test_dashboard_shows_margin_with_permission(self, auth_client, admin_user):
        client = auth_client(admin_user)
        response = client.get("/api/v1/reporting/dashboard/")
        assert "daily_margin" in response.json()["sales"]

    def test_csv_export_carries_bom_and_semicolons(
        self, auth_client, admin_user, batch
    ):
        """
        Excel on a French locale needs the BOM to detect UTF-8 and semicolons
        to split columns — comma-delimited output would land in one column.
        """
        client = auth_client(admin_user)
        response = client.get(
            "/api/v1/reporting/inventory/valuation/", {"export": "csv"}
        )
        assert response.status_code == 200
        assert response.content[:3] == b"\xef\xbb\xbf"
        assert b";" in response.content

    def test_excel_export_is_a_zip_container(self, auth_client, admin_user, batch):
        client = auth_client(admin_user)
        response = client.get(
            "/api/v1/reporting/inventory/valuation/", {"export": "xlsx"}
        )
        assert response.status_code == 200
        assert response.content[:2] == b"PK"

    def test_recall_trace_requires_batch_number(self, auth_client, admin_user):
        client = auth_client(admin_user)
        assert client.get("/api/v1/sales/recall-trace/").status_code == 422


class TestAuditTrail:
    def test_entries_are_immutable(self, admin_user, product):
        entry = AuditLog.objects.first()
        entry.notes = "tampered"
        with pytest.raises(RuntimeError, match="immutable"):
            entry.save()

    def test_entries_cannot_be_deleted(self, admin_user, product):
        with pytest.raises(RuntimeError, match="cannot be deleted"):
            AuditLog.objects.first().delete()

    def test_hash_chain_verifies(self, admin_user, product, batch):
        result = verify_chain()
        assert result["valid"] is True
        assert result["checked"] > 0

    def test_sensitive_fields_are_redacted(self, admin_user):
        from apps.core.audit import snapshot

        data = snapshot(admin_user)
        assert data["password"] == "***REDACTED***"

    def test_price_change_is_audited(self, product, admin_user):
        from apps.catalog.services import change_price

        change_price(
            product,
            selling_price=Decimal("2000"),
            reason="Hausse fournisseur",
            actor=admin_user,
        )
        entry = AuditLog.objects.filter(action=AuditAction.PRICE_CHANGE).first()
        assert entry is not None
        assert "Hausse fournisseur" in entry.notes

    def test_below_cost_pricing_is_flagged_in_the_note(self, product, admin_user):
        """
        Selling below cost is legitimate for clearance, so it warns rather
        than blocks — but the audit note records it prominently.
        """
        from apps.catalog.services import change_price

        change_price(
            product,
            selling_price=Decimal("100"),
            reason="Déstockage péremption proche",
            actor=admin_user,
        )
        entry = AuditLog.objects.filter(action=AuditAction.PRICE_CHANGE).first()
        assert "below cost" in entry.notes


class TestPurchasing:
    @pytest.fixture
    def submitted_order(self, supplier, warehouse, product, inventory_officer):
        from apps.purchasing.services import create_order, submit_for_approval

        order = create_order(
            supplier=supplier,
            warehouse=warehouse,
            actor=inventory_officer,
            freight_cost=Decimal("50000"),
            lines=[
                {
                    "product": product,
                    "quantity_ordered": Decimal("200"),
                    "unit_cost": Decimal("1000"),
                }
            ],
        )
        submit_for_approval(order, actor=inventory_officer)
        return order

    def test_unapproved_supplier_is_refused(
        self, warehouse, product, admin_user, inventory_officer
    ):
        """
        Sourcing pharmaceuticals from an unvetted supplier is a quality risk
        and a regulatory finding, so it is blocked at order creation.
        """
        from apps.partners.services import create_supplier
        from apps.purchasing.services import create_order

        unvetted = create_supplier(actor=admin_user, name="Non agréé")

        with pytest.raises(BusinessRuleViolation) as exc:
            create_order(
                supplier=unvetted, warehouse=warehouse, actor=inventory_officer,
                lines=[
                    {
                        "product": product,
                        "quantity_ordered": Decimal("1"),
                        "unit_cost": Decimal("1"),
                    }
                ],
            )
        assert exc.value.code == "supplier_not_approved"

    def test_self_approval_is_blocked(self, submitted_order, inventory_officer):
        """
        Separation of duties: the person who raised the order must not be the
        person who authorises it. This is the control that prevents a single
        employee from both creating and approving a purchase.
        """
        from apps.purchasing.services import approve_order

        with pytest.raises(BusinessRuleViolation) as exc:
            approve_order(submitted_order, actor=inventory_officer)
        assert exc.value.code == "separation_of_duties"

    def test_approval_by_another_user_succeeds(self, submitted_order, store_manager):
        from apps.purchasing.models import PurchaseOrderStatus
        from apps.purchasing.services import approve_order

        approve_order(submitted_order, actor=store_manager)
        assert submitted_order.status == PurchaseOrderStatus.APPROVED

    def test_receipt_apportions_landed_cost(
        self, submitted_order, store_manager, today
    ):
        """
        Ignoring freight and duty understates COGS and overstates margin —
        how a wholesaler convinces itself it is profitable when it is not.
        """
        from apps.purchasing.services import approve_order, receive_goods

        approve_order(submitted_order, actor=store_manager)
        line = submitted_order.lines.first()

        receipt = receive_goods(
            submitted_order,
            actor=store_manager,
            quality_checked=True,
            lines=[
                {
                    "purchase_order_line": line,
                    "batch_number": "PO-BATCH",
                    "expiry_date": today + timedelta(days=400),
                    "quantity_received": Decimal("200"),
                    "unit_cost": Decimal("1000"),
                }
            ],
        )
        receipt_line = receipt.lines.first()
        # 50,000 freight over 200 units = 250/unit on top of 1,000.
        assert receipt_line.landed_unit_cost == Decimal("1250.0000")

    def test_over_receipt_is_refused(self, submitted_order, store_manager, today):
        from apps.purchasing.services import approve_order, receive_goods

        approve_order(submitted_order, actor=store_manager)
        line = submitted_order.lines.first()

        with pytest.raises(BusinessRuleViolation) as exc:
            receive_goods(
                submitted_order,
                actor=store_manager,
                lines=[
                    {
                        "purchase_order_line": line,
                        "batch_number": "TOO-MUCH",
                        "expiry_date": today + timedelta(days=400),
                        "quantity_received": Decimal("500"),
                    }
                ],
            )
        assert exc.value.code == "over_receipt"

    def test_short_dated_delivery_is_refused(
        self, supplier, warehouse, product, inventory_officer, store_manager, today
    ):
        """
        Short-dated stock arrives sellable but becomes a write-off before it
        can move, so an order line may state a minimum acceptable expiry.
        """
        from apps.purchasing.services import (
            approve_order,
            create_order,
            receive_goods,
            submit_for_approval,
        )

        order = create_order(
            supplier=supplier, warehouse=warehouse, actor=inventory_officer,
            lines=[
                {
                    "product": product,
                    "quantity_ordered": Decimal("10"),
                    "unit_cost": Decimal("1000"),
                    "expected_expiry_date": today + timedelta(days=300),
                }
            ],
        )
        submit_for_approval(order, actor=inventory_officer)
        approve_order(order, actor=store_manager)

        with pytest.raises(BusinessRuleViolation) as exc:
            receive_goods(
                order,
                actor=store_manager,
                lines=[
                    {
                        "purchase_order_line": order.lines.first(),
                        "batch_number": "SHORT",
                        "expiry_date": today + timedelta(days=30),
                        "quantity_received": Decimal("10"),
                    }
                ],
            )
        assert exc.value.code == "expiry_below_minimum"

    def test_cannot_approve_a_draft(self, supplier, warehouse, product,
                                    inventory_officer, store_manager):
        from apps.purchasing.services import approve_order, create_order

        order = create_order(
            supplier=supplier, warehouse=warehouse, actor=inventory_officer,
            lines=[
                {
                    "product": product,
                    "quantity_ordered": Decimal("1"),
                    "unit_cost": Decimal("1"),
                }
            ],
        )
        with pytest.raises(InvalidStateTransition):
            approve_order(order, actor=store_manager)


class TestNumbering:
    def test_sequences_are_gap_free_and_unique(self, db):
        """
        A tax authority reads a missing invoice number as a suppressed sale,
        so the series must be dense.
        """
        from apps.core.numbering import next_number

        numbers = [next_number("sales.invoice") for _ in range(5)]
        assert len(set(numbers)) == 5

        suffixes = [int(number.split("-")[-1]) for number in numbers]
        assert suffixes == list(range(suffixes[0], suffixes[0] + 5))

    def test_unknown_sequence_raises(self, db):
        from apps.core.numbering import next_number

        with pytest.raises(ValueError, match="not configured"):
            next_number("does.not.exist")


class TestValidators:
    @pytest.mark.parametrize(
        "value", ["4000123456", "4000 123 456", "1234A5678", "4000-123-456"]
    )
    def test_valid_nif_accepted(self, value):
        from apps.core.validators import validate_nif

        validate_nif(value)  # must not raise

    @pytest.mark.parametrize("value", ["ABCD1234", "123", "40001234567890"])
    def test_invalid_nif_rejected(self, value):
        from django.core.exceptions import ValidationError

        from apps.core.validators import validate_nif

        with pytest.raises(ValidationError):
            validate_nif(value)

    @pytest.mark.parametrize(
        "value,expected",
        [
            # Bare 8-digit input is assumed Burundian and gains the +257 code.
            ("79123456", "+25779123456"),
            ("+257 79 123 456", "+25779123456"),
            ("257-79-123-456", "+25779123456"),
            # Foreign numbers keep their own country code.
            ("+44 20 7946 0958", "+442079460958"),
            ("+1 (415) 555-0132", "+14155550132"),
            ("+86 10 6552 9988", "+861065529988"),
            ("", ""),
        ],
    )
    def test_phone_normalisation(self, value, expected):
        from apps.core.validators import normalise_phone

        assert normalise_phone(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "68606080",  # Burundi local
            "+25768606080",
            "+44 20 7946 0958",  # United Kingdom
            "+1 (415) 555-0132",  # United States
            "+91 98765 43210",  # India
            "+254 712 345678",  # Kenya
            "+971 4 123 4567",  # United Arab Emirates
            "",  # optional field
        ],
    )
    def test_valid_phone_accepted(self, value):
        from apps.core.validators import validate_phone

        validate_phone(value)  # must not raise

    @pytest.mark.parametrize(
        "value",
        [
            "123456",  # too short for an international number
            "1234567890123456",  # 16 digits, beyond the E.164 maximum
            "+0123456789",  # no country code begins with zero
            "079123456",  # national trunk prefix, not international
            "+257 79 123 45A",  # letters are not permitted
        ],
    )
    def test_invalid_phone_rejected(self, value):
        from django.core.exceptions import ValidationError

        from apps.core.validators import validate_phone

        with pytest.raises(ValidationError):
            validate_phone(value)


class TestTranslations:
    def test_french_resolves(self):
        from django.utils import translation

        with translation.override("fr"):
            from django.utils.translation import gettext

            assert gettext("invoice") == "facture"
            assert (
                gettext("Insufficient stock available to fulfil this request.")
                == "Stock insuffisant pour satisfaire cette demande."
            )

    def test_phone_error_is_translated(self):
        """
        The compiled catalogue must track the validator's source string. If the
        two drift apart the message silently falls back to English for French
        users, which is easy to miss because nothing raises.
        """
        from django.core.exceptions import ValidationError
        from django.utils import translation

        from apps.core.validators import validate_phone

        with translation.override("fr"):
            with pytest.raises(ValidationError) as caught:
                validate_phone("+257 79 123 45A")

        assert caught.value.messages[0].startswith(
            "Saisissez un numéro de téléphone valide au format international"
        )

    def test_english_falls_back_to_source(self):
        """Source strings are English, so English needs no catalogue entries."""
        from django.utils import translation

        with translation.override("en"):
            from django.utils.translation import gettext

            assert gettext("invoice") == "invoice"
