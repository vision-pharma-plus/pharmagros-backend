"""
HTTP-level smoke test of the API.

Drives the real stack through Django's test client — routing, JWT auth, RBAC
enforcement, serializers and services — rather than calling services directly.
This is what proves the API layer is wired, not merely that the domain logic
works.

The RBAC assertions matter most: they confirm that a technician really cannot
reach a privileged endpoint, rather than that we merely intended it.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from django.test import Client
from django.utils import timezone

RESULTS: list[tuple[bool, str, str]] = []


def check(label, got, want):
    ok = got == want
    RESULTS.append((ok, label, f"got={got!r} want={want!r}"))
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got} want={want}")
    return ok


def check_in(label, got, allowed):
    ok = got in allowed
    RESULTS.append((ok, label, f"got={got!r} allowed={allowed!r}"))
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got={got} allowed={allowed}")
    return ok


class ApiClient:
    """Thin JSON wrapper that carries a bearer token."""

    def __init__(self, token: str | None = None):
        self.client = Client()
        self.token = token

    def _headers(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"} if self.token else {}

    def get(self, path, **params):
        return self.client.get(path, params, **self._headers())

    def post(self, path, payload=None):
        return self.client.post(
            path,
            data=json.dumps(payload or {}, default=str),
            content_type="application/json",
            **self._headers(),
        )

    def patch(self, path, payload):
        return self.client.patch(
            path,
            data=json.dumps(payload, default=str),
            content_type="application/json",
            **self._headers(),
        )


def login(email: str, password: str) -> str | None:
    response = Client().post(
        "/api/v1/auth/login/",
        data=json.dumps({"email": email, "password": password}),
        content_type="application/json",
    )
    if response.status_code != 200:
        print(f"    login failed for {email}: {response.status_code} {response.content[:200]}")
        return None
    return response.json()["access"]


def run() -> bool:
    from apps.accounts.models import Role, User
    from apps.catalog.models import Category, UnitOfMeasure
    from apps.inventory.models import Warehouse

    today = timezone.localdate()

    print("=" * 70)
    print("SETUP")
    print("=" * 70)

    admin = User.objects.create_user(
        email="api-admin@pharmagros.bi", password="ApiAdmin!2026#Sec",
        first_name="Admin", last_name="API",
    )
    admin.roles.add(Role.objects.get(code="system-administrator"))

    tech = User.objects.create_user(
        email="api-tech@pharmagros.bi", password="ApiTech!2026#Sec",
        first_name="Tech", last_name="API",
    )
    tech.roles.add(Role.objects.get(code="pharmacy-technician"))

    auditor = User.objects.create_user(
        email="api-auditor@pharmagros.bi", password="ApiAudit!2026#Sec",
        first_name="Auditor", last_name="API",
    )
    auditor.roles.add(Role.objects.get(code="auditor"))

    warehouse = Warehouse.objects.create(
        code="API-WH", name_fr="Entrepôt API", city="Bujumbura",
    )
    category = Category.objects.create(code="APICAT", name_fr="Catégorie API")
    unit = UnitOfMeasure.objects.create(code="APIU", name_fr="Unité")

    print("Users and reference data created.")

    print()
    print("=" * 70)
    print("AUTHENTICATION")
    print("=" * 70)

    anon = ApiClient()
    check("unauthenticated request rejected", anon.get("/api/v1/catalog/medicines/").status_code, 401)

    bad = Client().post(
        "/api/v1/auth/login/",
        data=json.dumps({"email": "api-admin@pharmagros.bi", "password": "wrong"}),
        content_type="application/json",
    )
    check_in("bad password rejected", bad.status_code, [400, 401])

    admin_token = login("api-admin@pharmagros.bi", "ApiAdmin!2026#Sec")
    check("admin login succeeds", admin_token is not None, True)
    tech_token = login("api-tech@pharmagros.bi", "ApiTech!2026#Sec")
    auditor_token = login("api-auditor@pharmagros.bi", "ApiAudit!2026#Sec")

    api = ApiClient(admin_token)
    tech_api = ApiClient(tech_token)
    auditor_api = ApiClient(auditor_token)

    me = api.get("/api/v1/auth/me/")
    check("profile endpoint", me.status_code, 200)
    check("profile carries permissions", len(me.json()["permissions"]) > 0, True)
    check("admin sees all 75 permissions", len(me.json()["permissions"]), 75)

    tech_me = tech_api.get("/api/v1/auth/me/")
    check("technician sees 11 permissions", len(tech_me.json()["permissions"]), 11)

    lang = api.post("/api/v1/auth/language/", {"language": "en"})
    check("language switch", lang.status_code, 200)
    api.post("/api/v1/auth/language/", {"language": "fr"})

    print()
    print("=" * 70)
    print("RBAC ENFORCEMENT")
    print("=" * 70)

    check(
        "technician blocked from creating medicine",
        tech_api.post("/api/v1/catalog/medicines/", {"name": "X"}).status_code,
        403,
    )
    check(
        "technician blocked from user admin",
        tech_api.get("/api/v1/auth/users/").status_code,
        403,
    )
    check(
        "technician blocked from valuation",
        tech_api.get("/api/v1/inventory/valuation/").status_code,
        403,
    )
    check(
        "technician can list medicines",
        tech_api.get("/api/v1/catalog/medicines/").status_code,
        200,
    )
    check(
        "auditor can read audit log",
        auditor_api.get("/api/v1/core/audit-logs/").status_code,
        200,
    )
    check(
        "auditor blocked from creating a sale",
        auditor_api.post("/api/v1/sales/sales/", {}).status_code,
        403,
    )
    check(
        "auditor blocked from receiving stock",
        auditor_api.post("/api/v1/inventory/receive/", {}).status_code,
        403,
    )

    print()
    print("=" * 70)
    print("CATALOG")
    print("=" * 70)

    created = api.post(
        "/api/v1/catalog/medicines/",
        {
            "name_fr": "Ibuprofène API",
            "generic_name": "Ibuprofen",
            "strength_fr": "400 mg",
            "dosage_form": "TABLET",
            "category": str(category.id),
            "unit_of_measure": str(unit.id),
            "unit_cost": "3000.0000",
            "selling_price": "4500.0000",
            "vat_rate": "18.0000",
            "reorder_level": "50.000",
            # Batch and expiry are captured at product entry; supplying them
            # seeds an opening stock batch so the product is sellable at once.
            "batch_number": "IBU-API-001",
            "expiry_date": "2028-12-31",
        },
    )
    if created.status_code != 201:
        print(f"    RESPONSE: {created.content[:600]}")
    check("create medicine", created.status_code, 201)
    product_id = created.json()["id"]
    product_code = created.json()["product_code"]
    check("product code auto-allocated", product_code.startswith("MED-"), True)

    check("list medicines", api.get("/api/v1/catalog/medicines/").status_code, 200)
    check(
        "search by generic name finds it",
        api.get("/api/v1/catalog/medicines/", search="Ibuprofen").json()["count"] >= 1,
        True,
    )

    no_reason = api.post(f"/api/v1/catalog/medicines/{product_id}/change-price/",
                         {"selling_price": "5000.0000"})
    check("price change without reason rejected", no_reason.status_code, 400)

    with_reason = api.post(
        f"/api/v1/catalog/medicines/{product_id}/change-price/",
        {"selling_price": "5000.0000", "reason": "Hausse du coût fournisseur"},
    )
    check("price change with reason accepted", with_reason.status_code, 200)
    check(
        "price history recorded",
        api.get(f"/api/v1/catalog/medicines/{product_id}/price-history/").json()["count"],
        2,
    )

    print()
    print("=" * 70)
    print("PARTNERS")
    print("=" * 70)

    cust = api.post(
        "/api/v1/partners/customers/",
        {
            "business_name": "Clinique API SARL",
            "customer_type": "CLINIC",
            "nif": "4000987654",
            "phone": "79987654",
            "payment_terms": "NET_30",
            "credit_limit": "2000000.0000",
        },
    )
    check("create customer", cust.status_code, 201)
    customer_id = cust.json()["id"]
    check("NIF normalised", cust.json()["nif"], "4000987654")

    no_nif = api.post(
        "/api/v1/partners/customers/",
        {"business_name": "Sans NIF", "customer_type": "PHARMACY", "payment_terms": "NET_30"},
    )
    check("credit customer without NIF rejected", no_nif.status_code, 400)

    supp = api.post(
        "/api/v1/partners/suppliers/",
        {"name": "Fournisseur API", "country": "Kenya", "payment_terms": "NET_30"},
    )
    check("create supplier", supp.status_code, 201)
    supplier_id = supp.json()["id"]
    check("supplier not approved by default", supp.json()["is_approved"], False)

    approved = api.post(f"/api/v1/partners/suppliers/{supplier_id}/approve/",
                        {"notes": "Dossier vérifié"})
    check("approve supplier", approved.json()["is_approved"], True)

    print()
    print("=" * 70)
    print("PURCHASING")
    print("=" * 70)

    order = api.post(
        "/api/v1/purchasing/orders/",
        {
            "supplier": supplier_id,
            "warehouse": str(warehouse.id),
            "freight_cost": "80000.0000",
            "lines": [
                {
                    "product": product_id,
                    "quantity_ordered": "400.000",
                    "unit_cost": "3000.0000",
                    "tax_rate": "18.0000",
                }
            ],
        },
    )
    check("create purchase order", order.status_code, 201)
    order_id = order.json()["id"]
    po_line_id = order.json()["lines"][0]["id"]

    check("submit for approval",
          api.post(f"/api/v1/purchasing/orders/{order_id}/submit/").status_code, 200)

    # The admin raised this order, so separation of duties must block them.
    self_approve = api.post(f"/api/v1/purchasing/orders/{order_id}/approve/")
    check("self-approval blocked (separation of duties)", self_approve.status_code, 422)
    check("correct error code", self_approve.json()["error"]["code"], "separation_of_duties")

    # A second privileged user approves it.
    approver = User.objects.create_user(
        email="api-approver@pharmagros.bi", password="ApiAppr!2026#Sec",
        first_name="Appro", last_name="Ver",
    )
    approver.roles.add(Role.objects.get(code="store-manager"))
    approver_api = ApiClient(login("api-approver@pharmagros.bi", "ApiAppr!2026#Sec"))

    ok_approve = approver_api.post(f"/api/v1/purchasing/orders/{order_id}/approve/")
    check("approval by a different user succeeds", ok_approve.status_code, 200)
    check("order approved", ok_approve.json()["status"], "APPROVED")

    receipt = approver_api.post(
        f"/api/v1/purchasing/orders/{order_id}/receive/",
        {
            "quality_checked": True,
            "lines": [
                {
                    "purchase_order_line": po_line_id,
                    "batch_number": "API-B1",
                    "expiry_date": str(today + timedelta(days=400)),
                    "quantity_received": "400.000",
                    "unit_cost": "3000.0000",
                }
            ],
        },
    )
    if receipt.status_code != 201:
        print(f"    RESPONSE: {receipt.content[:600]}")
    check("receive goods", receipt.status_code, 201)
    check("batch created with landed cost",
          Decimal(receipt.json()["lines"][0]["landed_unit_cost"]) > Decimal("3000"), True)

    print()
    print("=" * 70)
    print("INVENTORY")
    print("=" * 70)

    # Paginated, so the product is searched for by code rather than scanned
    # out of a full-catalogue array.
    levels = api.get(f"/api/v1/inventory/stock-levels/?search={product_code}")
    check("stock levels endpoint", levels.status_code, 200)
    entry = next(
        (r for r in levels.json()["results"] if r["product_id"] == product_id), None
    )
    # 400 from the purchase receipt above, plus the 1000-unit opening batch
    # seeded automatically because the product was created with a batch
    # number and expiry date.
    check("stock level reflects receipt", Decimal(entry["quantity_available"]), Decimal("1400.000"))

    valuation = api.get("/api/v1/inventory/valuation/")
    check("valuation endpoint", valuation.status_code, 200)
    check("valuation non-zero", Decimal(valuation.json()["total_value"]) > 0, True)

    batches = api.get("/api/v1/inventory/batches/", search="API-B1")
    check("batch listing", batches.status_code, 200)
    check("batch listing", batches.json()["count"], 1)
    batch_id = batches.json()["results"][0]["id"]

    check("ledger listing", api.get("/api/v1/inventory/movements/").status_code, 200)

    no_reason_adj = api.post(f"/api/v1/inventory/batches/{batch_id}/adjust/",
                             {"new_quantity": "395.000"})
    check("adjustment without reason rejected", no_reason_adj.status_code, 400)

    adj = api.post(
        f"/api/v1/inventory/batches/{batch_id}/adjust/",
        {"new_quantity": "395.000", "reason": "Inventaire physique — 5 unités manquantes"},
    )
    check("adjustment with reason accepted", adj.status_code, 200)

    recon = api.get("/api/v1/inventory/reconciliation/")
    check("reconciliation clean after adjustment", recon.json()["discrepancy_count"], 0)

    print()
    print("=" * 70)
    print("SALES + INVOICING")
    print("=" * 70)

    sale = api.post(
        "/api/v1/sales/sales/",
        {
            "customer": customer_id,
            "warehouse": str(warehouse.id),
            "sale_type": "CREDIT",
            "lines": [{"product": product_id, "quantity": "100.000"}],
        },
    )
    check("create sale", sale.status_code, 201)
    sale_id = sale.json()["id"]
    check("sale starts as draft", sale.json()["status"], "DRAFT")

    confirmed = api.post(f"/api/v1/sales/sales/{sale_id}/confirm/", {"generate_invoice": True})
    check("confirm sale", confirmed.status_code, 200)
    check("sale confirmed", confirmed.json()["status"], "CONFIRMED")
    check("invoice generated", confirmed.json()["invoice_number"] is not None, True)
    invoice_id = confirmed.json()["invoice_id"]

    invoice = api.get(f"/api/v1/invoicing/invoices/{invoice_id}/")
    check("invoice retrievable", invoice.status_code, 200)
    check("invoice posted", invoice.json()["status"], "POSTED")
    check("batch numbers on invoice line",
          invoice.json()["lines"][0]["batch_numbers"], "API-B1")
    # 100 x 5000 = 500,000 + 18% VAT = 590,000
    check("invoice total", Decimal(invoice.json()["total_amount"]), Decimal("590000.0000"))

    # Credit limit is 2,000,000 and 590,000 is used.
    over = api.post(
        "/api/v1/sales/sales/",
        {
            "customer": customer_id,
            "warehouse": str(warehouse.id),
            "sale_type": "CREDIT",
            "lines": [{"product": product_id, "quantity": "100.000", "unit_price": "50000.0000"}],
        },
    )
    over_confirm = api.post(f"/api/v1/sales/sales/{over.json()['id']}/confirm/", {})
    check("over-limit sale blocked", over_confirm.status_code, 422)
    check("credit limit error code",
          over_confirm.json()["error"]["code"], "credit_limit_exceeded")

    payment = api.post(
        "/api/v1/invoicing/payments/",
        {
            "customer": customer_id,
            "amount": "300000.0000",
            "method": "BANK_TRANSFER",
            "bank_reference": "VIR-API-001",
        },
    )
    check("record payment", payment.status_code, 201)

    invoice = api.get(f"/api/v1/invoicing/invoices/{invoice_id}/")
    check("invoice partially paid", invoice.json()["status"], "PARTIALLY_PAID")
    check("balance after payment",
          Decimal(invoice.json()["balance_due"]), Decimal("290000.0000"))

    statement = api.get(f"/api/v1/partners/customers/{customer_id}/statement/")
    check("customer statement", statement.status_code, 200)
    check("statement closing balance",
          Decimal(statement.json()["closing_balance"]), Decimal("290000.0000"))

    print()
    print("=" * 70)
    print("RECALL TRACE")
    print("=" * 70)

    trace = api.get("/api/v1/sales/recall-trace/", batch_number="API-B1")
    check("recall trace", trace.status_code, 200)
    check("one recipient found", trace.json()["recipient_count"], 1)
    check("customer identified",
          trace.json()["recipients"][0]["customer_name"], "Clinique API SARL")

    no_batch = api.get("/api/v1/sales/recall-trace/")
    check("recall trace requires a batch number", no_batch.status_code, 422)

    print()
    print("=" * 70)
    print("REPORTING")
    print("=" * 70)

    dash = api.get("/api/v1/reporting/dashboard/")
    check("dashboard", dash.status_code, 200)
    check("dashboard has inventory KPIs", "total_products" in dash.json()["inventory"], True)
    check("dashboard has receivables", "outstanding_total" in dash.json()["receivables"], True)

    widgets = api.get("/api/v1/reporting/dashboard/widgets/")
    check("dashboard widgets", widgets.status_code, 200)
    check("widgets include trends", "revenue_trend" in widgets.json(), True)

    for name, path in [
        ("valuation", "/api/v1/reporting/inventory/valuation/"),
        ("expiry", "/api/v1/reporting/inventory/expiry/"),
        ("movements", "/api/v1/reporting/inventory/movements/"),
        ("dead stock", "/api/v1/reporting/inventory/dead-stock/"),
        ("sales", "/api/v1/reporting/sales/"),
        ("ageing", "/api/v1/reporting/financial/receivables-ageing/"),
        ("profit/loss", "/api/v1/reporting/financial/profit-loss/"),
        ("compliance", "/api/v1/reporting/compliance/"),
    ]:
        check(f"report: {name}", api.get(path).status_code, 200)

    csv_export = api.get("/api/v1/reporting/inventory/valuation/", export="csv")
    check("CSV export", csv_export.status_code, 200)
    check("CSV content type", "text/csv" in csv_export["Content-Type"], True)
    check("CSV has BOM for Excel", csv_export.content[:3], b"\xef\xbb\xbf")
    check("CSV is semicolon-delimited", b";" in csv_export.content, True)

    xlsx = api.get("/api/v1/reporting/inventory/valuation/", export="xlsx")
    check("Excel export", xlsx.status_code, 200)
    check("Excel signature", xlsx.content[:2], b"PK")

    # Margin data must be hidden from users lacking the permission.
    tech_dash = tech_api.get("/api/v1/reporting/dashboard/")
    check("technician can see dashboard", tech_dash.status_code, 200)
    check("technician does not see margin",
          "daily_margin" in tech_dash.json()["sales"], False)
    check("technician does not see inventory value",
          "total_value" in tech_dash.json()["inventory"], False)
    check("admin sees margin", "daily_margin" in dash.json()["sales"], True)

    print()
    print("=" * 70)
    print("NOTIFICATIONS + AUDIT")
    print("=" * 70)

    check("notifications list", api.get("/api/v1/notifications/notifications/").status_code, 200)
    check("unread count",
          api.get("/api/v1/notifications/notifications/unread-count/").status_code, 200)
    check("announcements", api.get("/api/v1/notifications/announcements/active/").status_code, 200)

    audit = api.get("/api/v1/core/audit-logs/")
    check("audit log listing", audit.status_code, 200)
    check("audit entries recorded", audit.json()["count"] > 20, True)

    verify = api.post("/api/v1/core/audit-logs/verify/")
    check("audit chain verification endpoint", verify.status_code, 200)
    check("audit chain valid", verify.json()["valid"], True)

    # Audit trail must expose no write path at all.
    check_in("audit log has no create route",
             api.post("/api/v1/core/audit-logs/", {"action": "CREATE"}).status_code, [403, 405])

    check_in("stock ledger has no create route",
             api.post("/api/v1/inventory/movements/", {}).status_code, [403, 405])

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

    from django.test.utils import setup_test_environment, teardown_test_environment

    setup_test_environment()
    from django.test.runner import DiscoverRunner

    runner = DiscoverRunner(verbosity=0, interactive=False)
    old_config = runner.setup_databases()
    try:
        # RBAC and sequences must exist in the fresh test database.
        from django.core.management import call_command

        call_command("seed_rbac", verbosity=0)
        call_command("seed_sequences", verbosity=0)
        success = run()
    finally:
        runner.teardown_databases(old_config)
        teardown_test_environment()

    sys.exit(0 if success else 1)
