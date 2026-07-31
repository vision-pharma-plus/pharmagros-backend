"""
End-to-end integration test of the core commercial flow.

Exercises the real database, real services and real transactions:

    supplier + product + customer
      -> purchase order -> approval -> goods receipt (batches created)
      -> credit sale -> FIFO issue -> invoice posted
      -> payment -> balance settled
      -> return -> credit note
      -> recall trace

Written as a standalone runner rather than pytest cases so it can be executed
with `manage.py runscript`-style invocation during development, and reused as
the smoke test in CI.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

RESULTS: list[tuple[bool, str, str]] = []


def check(label: str, got, want) -> bool:
    ok = got == want
    RESULTS.append((ok, label, f"got={got!r} want={want!r}"))
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got} want={want}")
    return ok


def check_true(label: str, value) -> bool:
    return check(label, bool(value), True)


def run() -> bool:
    from apps.accounts.models import Role, User
    from apps.catalog.models import Category, Medicine, UnitOfMeasure
    from apps.catalog.services import change_price, create_medicine
    from apps.core.audit import verify_chain
    from apps.core.models import AuditLog
    from apps.inventory.models import BatchStatus, StockBatch, StockMovement
    from apps.inventory.services import find_discrepancies, inventory_valuation
    from apps.invoicing.models import Invoice, InvoiceStatus
    from apps.invoicing.services import record_payment
    from apps.partners.models import CustomerType, PaymentTerms
    from apps.partners.services import create_customer, create_supplier, approve_supplier
    from apps.purchasing.services import approve_order, create_order, receive_goods, submit_for_approval
    from apps.sales.models import SaleType
    from apps.sales.services import confirm_sale, create_sale, process_return, trace_batch_recipients

    today = timezone.localdate()

    print("=" * 70)
    print("SETUP: users, warehouse, catalogue, partners")
    print("=" * 70)

    buyer = User.objects.create_user(
        email="acheteur@pharmagros.bi", password="Achat!2026#Secure",
        first_name="Aline", last_name="Nkurunziza",
    )
    buyer.roles.add(Role.objects.get(code="inventory-officer"))

    manager = User.objects.create_user(
        email="responsable@pharmagros.bi", password="Gestion!2026#Sec",
        first_name="Didier", last_name="Bizimana",
    )
    manager.roles.add(Role.objects.get(code="store-manager"))

    seller = User.objects.create_user(
        email="pharmacien@pharmagros.bi", password="Pharma!2026#Sec",
        first_name="Chantal", last_name="Irakoze",
    )
    seller.roles.add(Role.objects.get(code="pharmacist"))

    from apps.inventory.models import Warehouse

    warehouse = Warehouse.objects.create(
        code="BJM-01", name_fr="Entrepôt principal Bujumbura", is_default=True,
        city="Bujumbura", is_cold_chain=True,
    )

    category = Category.objects.create(code="ANTIB", name_fr="Antibiotiques", name_en="Antibiotics")
    unit = UnitOfMeasure.objects.create(code="BTE", name_fr="Boîte", name_en="Box")

    product = create_medicine(
        actor=manager,
        name="Amoxicilline",
        generic_name="Amoxicillin",
        strength="500 mg",
        dosage_form="CAPSULE",
        category=category,
        unit_of_measure=unit,
        unit_cost=Decimal("4200"),
        selling_price=Decimal("6000"),
        wholesale_price=Decimal("5500"),
        vat_rate=Decimal("18"),
        reorder_level=Decimal("100"),
    )
    check_true("product code allocated", product.product_code.startswith("MED-"))

    supplier = create_supplier(
        actor=manager, name="Laboratoires Kampala Ltd", country="Ouganda",
        payment_terms=PaymentTerms.NET_30, lead_time_days=21,
    )
    approve_supplier(supplier, notes="Dossier qualité vérifié", actor=manager)
    check_true("supplier approved", supplier.is_approved)

    customer = create_customer(
        actor=manager,
        business_name="Pharmacie du Lac SARL",
        customer_type=CustomerType.PHARMACY,
        nif="4000123456",
        email="contact@pharmaciedulac.bi",
        phone="79123456",
        address="Avenue de l'Indépendance",
        credit_limit=Decimal("5000000"),
        payment_terms=PaymentTerms.NET_30,
    )
    check_true("customer code allocated", customer.customer_code.startswith("CLI-"))
    check("NIF normalised", customer.nif, "4000123456")
    check("phone normalised", customer.phone, "+25779123456")

    print()
    print("=" * 70)
    print("PROCUREMENT: order -> approval -> receipt")
    print("=" * 70)

    order = create_order(
        supplier=supplier, warehouse=warehouse, actor=buyer,
        freight_cost=Decimal("150000"), customs_duty=Decimal("90000"),
        lines=[{
            "product": product,
            "quantity_ordered": Decimal("500"),
            "unit_cost": Decimal("4200"),
            "tax_rate": Decimal("18"),
        }],
    )
    check_true("PO number allocated", order.order_number.startswith("BC-"))
    submit_for_approval(order, actor=buyer)

    # Separation of duties: the requester must not be able to approve.
    sod_blocked = False
    try:
        approve_order(order, actor=buyer)
    except Exception as exc:
        sod_blocked = getattr(exc, "code", "") == "separation_of_duties"
    check_true("separation of duties enforced", sod_blocked)

    approve_order(order, actor=manager)
    check("PO approved", order.status, "APPROVED")

    po_line = order.lines.first()
    receipt = receive_goods(
        order, actor=buyer, quality_checked=True,
        delivery_note_number="BL-UG-8891",
        lines=[{
            "purchase_order_line": po_line,
            "batch_number": "AMX-2026-A",
            "expiry_date": today + timedelta(days=540),
            "manufacturing_date": today - timedelta(days=30),
            "quantity_received": Decimal("300"),
            "unit_cost": Decimal("4200"),
        }],
    )
    check_true("receipt number allocated", receipt.receipt_number.startswith("BR-"))

    batch1 = StockBatch.objects.get(batch_number="AMX-2026-A")
    check("batch1 on hand", batch1.quantity_remaining, Decimal("300.000"))
    # Landed cost must exceed unit cost: freight + duty apportioned in.
    check_true("landed cost includes import charges", batch1.landed_unit_cost > Decimal("4200"))
    print(f"        unit_cost=4200  landed_unit_cost={batch1.landed_unit_cost}")

    # Second, shorter-dated delivery against the same order.
    receive_goods(
        order, actor=buyer, quality_checked=True,
        lines=[{
            "purchase_order_line": po_line,
            "batch_number": "AMX-2026-B",
            "expiry_date": today + timedelta(days=120),
            "quantity_received": Decimal("200"),
            "unit_cost": Decimal("4200"),
        }],
    )
    batch2 = StockBatch.objects.get(batch_number="AMX-2026-B")
    order.refresh_from_db()
    check("PO fully received", order.status, "RECEIVED")

    valuation = inventory_valuation(warehouse=warehouse)
    check("valuation counts 500 units", valuation["total_units"], Decimal("500.0000"))
    print(f"        inventory value = {valuation['total_value']} BIF")

    print()
    print("=" * 70)
    print("SALE: credit sale -> FEFO issue -> invoice")
    print("=" * 70)

    sale = create_sale(
        customer=customer, warehouse=warehouse, sale_type=SaleType.CREDIT,
        actor=seller,
        lines=[{"product": product, "quantity": Decimal("250")}],
    )
    check_true("sale number allocated", sale.sale_number.startswith("CMD-"))
    # Institutional? No - PHARMACY is not institutional, so standard price.
    check("unit price = selling price", sale.lines.first().unit_price, Decimal("6000.0000"))

    sale, invoice = confirm_sale(sale, actor=seller)
    check("sale confirmed", sale.status, "CONFIRMED")
    check_true("invoice generated", invoice is not None)
    check("invoice posted", invoice.status, InvoiceStatus.POSTED)
    check_true("invoice number allocated", invoice.invoice_number.startswith("FAC-"))
    check("customer NIF frozen on invoice", invoice.customer_nif, "4000123456")

    # FEFO: the 120-day batch must be consumed before the 540-day batch.
    allocations = list(sale.lines.first().batch_allocations.order_by("expiry_date"))
    check("issued from 2 batches", len(allocations), 2)
    check("shortest-dated batch first", allocations[0].batch_number, "AMX-2026-B")
    check("took all 200 of short-dated", allocations[0].quantity, Decimal("200.000"))
    check("took 50 from long-dated", allocations[1].quantity, Decimal("50.000"))

    batch1.refresh_from_db(); batch2.refresh_from_db()
    check("batch2 depleted", batch2.quantity_remaining, Decimal("0.000"))
    check("batch2 status DEPLETED", batch2.status, BatchStatus.DEPLETED)
    check("batch1 reduced to 250", batch1.quantity_remaining, Decimal("250.000"))

    # 250 * 6000 = 1,500,000 net; VAT 18% = 270,000; total 1,770,000
    check("invoice total", invoice.total_amount, Decimal("1770000.0000"))
    check("invoice VAT", invoice.tax_amount, Decimal("270000.0000"))
    check("balance due", invoice.balance_due, Decimal("1770000.0000"))

    customer.refresh_from_db()
    check("customer balance updated", customer.outstanding_balance, Decimal("1770000.0000"))
    check("available credit reduced", customer.available_credit, Decimal("3230000.0000"))

    print()
    print("=" * 70)
    print("CREDIT CONTROL: limit enforcement")
    print("=" * 70)

    big_sale = create_sale(
        customer=customer, warehouse=warehouse, sale_type=SaleType.CREDIT, actor=seller,
        lines=[{"product": product, "quantity": Decimal("200"), "unit_price": Decimal("30000")}],
    )
    blocked = False
    try:
        confirm_sale(big_sale, actor=seller)
    except Exception as exc:
        blocked = getattr(exc, "code", "") == "credit_limit_exceeded"
    check_true("over-limit credit sale blocked", blocked)

    print()
    print("=" * 70)
    print("PAYMENT")
    print("=" * 70)

    payment = record_payment(
        customer=customer, amount=Decimal("1000000"), method="BANK_TRANSFER",
        bank_reference="VIR-2026-4471", actor=seller,
    )
    check_true("receipt number allocated", payment.reference.startswith("REC-"))
    invoice.refresh_from_db(); customer.refresh_from_db()
    check("invoice partially paid", invoice.status, InvoiceStatus.PARTIALLY_PAID)
    check("balance after payment", invoice.balance_due, Decimal("770000.0000"))
    check("customer balance follows", customer.outstanding_balance, Decimal("770000.0000"))

    print()
    print("=" * 70)
    print("RETURN + CREDIT NOTE")
    print("=" * 70)

    sale_line = sale.lines.first()
    sale_return = process_return(
        sale, actor=seller, reason="Emballage endommagé à la livraison",
        lines=[{
            "sale_line": sale_line,
            "batch": allocations[1].batch,
            "quantity": Decimal("20"),
            "restock": False,               # damaged -> must not re-enter stock
            "condition_notes": "Boîtes écrasées",
        }],
    )
    check_true("return number allocated", sale_return.return_number.startswith("RET-"))
    check("refund amount", sale_return.total_amount, Decimal("120000.0000"))
    check_true("credit note issued", sale_return.credit_note is not None)
    check_true("credit note numbered", sale_return.credit_note.invoice_number.startswith("NC-"))

    batch1.refresh_from_db()
    # Not restocked: returned in then written off, so net balance is unchanged.
    check("damaged return not added to sellable stock", batch1.quantity_remaining, Decimal("250.000"))

    damage_moves = StockMovement.objects.filter(batch=batch1, movement_type="DAMAGE").count()
    check("damage write-off recorded", damage_moves, 1)

    sale.refresh_from_db()
    check("sale partially returned", sale.status, "PARTIALLY_RETURNED")

    print()
    print("=" * 70)
    print("TRACEABILITY: recall query")
    print("=" * 70)

    trace = trace_batch_recipients("AMX-2026-B")
    check("recall finds 1 recipient", len(trace), 1)
    check("recipient identified", trace[0]["customer_name"], "Pharmacie du Lac SARL")
    check("quantity supplied traced", trace[0]["quantity_supplied"], Decimal("200.000"))
    print(f"        {trace[0]['customer_name']} / {trace[0]['customer_phone']} "
          f"received {trace[0]['quantity_supplied']} units of batch AMX-2026-B")

    print()
    print("=" * 70)
    print("INTEGRITY: ledger reconciliation + audit chain")
    print("=" * 70)

    discrepancies = find_discrepancies()
    check("no stock discrepancies", len(discrepancies), 0)

    chain = verify_chain()
    check_true("audit chain valid", chain["valid"])
    print(f"        {chain['checked']} audit entries verified")

    # Audit immutability at the Python layer.
    entry = AuditLog.objects.first()
    immutable = False
    try:
        entry.notes = "tampered"
        entry.save()
    except RuntimeError:
        immutable = True
    check_true("audit entry rejects update", immutable)

    undeletable = False
    try:
        entry.delete()
    except RuntimeError:
        undeletable = True
    check_true("audit entry rejects delete", undeletable)

    # Stock movements are equally immutable.
    move = StockMovement.objects.first()
    move_immutable = False
    try:
        move.quantity_delta = Decimal("999")
        move.save()
    except RuntimeError:
        move_immutable = True
    check_true("stock movement rejects update", move_immutable)

    print()
    print("=" * 70)
    print("PRICE HISTORY")
    print("=" * 70)

    change_price(product, selling_price=Decimal("6500"),
                 reason="Hausse du coût d'importation", actor=manager)
    check("price history has 2 entries", product.price_history.count(), 2)

    reason_required = False
    try:
        change_price(product, selling_price=Decimal("7000"), reason="", actor=manager)
    except Exception as exc:
        reason_required = getattr(exc, "code", "") == "reason_required"
    check_true("price change requires a reason", reason_required)

    print()
    print("=" * 70)
    failed = [r for r in RESULTS if not r[0]]
    print(f"TOTAL: {len(RESULTS)} assertions, {len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("\nFAILURES:")
        for _ok, label, detail in failed:
            print(f"  - {label}: {detail}")
    print("=" * 70)
    return not failed


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.check")

    import django

    django.setup()

    from django.db import transaction

    # Roll everything back so the script can be re-run against the same
    # database without accumulating fixture data.
    try:
        with transaction.atomic():
            success = run()
            raise SystemExit(0 if success else 1)
    except SystemExit as exc:
        sys.exit(exc.code)
