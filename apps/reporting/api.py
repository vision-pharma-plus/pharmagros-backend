"""
Reporting endpoints.

Each report accepts `?format=json|csv|xlsx`. JSON is the default and feeds the
dashboard; the file formats are what an accountant or auditor actually asks
for. Column headers are translated at request time so an English user's export
is readable, while remaining line-for-line reconcilable with the French one.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record
from apps.core.models import AuditAction
from apps.core.permissions import HasPermission

from . import services
from .exporters import export
from .serializers import (
    ComplianceReportSerializer,
    DashboardSerializer,
    DashboardWidgetsSerializer,
    DeadStockReportSerializer,
    ExpiryReportSerializer,
    MovementReportSerializer,
    ProfitLossSerializer,
    ReceivablesAgeingSerializer,
    SalesReportSerializer,
    ValuationReportSerializer,
)


def _parse_date(value, default=None):
    if not value:
        return default
    from django.utils.dateparse import parse_date, parse_datetime

    return parse_datetime(value) or parse_date(value) or default


class _ReportView(APIView):
    """
    Base for report endpoints.

    Data exports are audited: knowing who extracted commercial data, and when,
    is a compliance requirement in its own right — exfiltration by an
    authorised user is otherwise invisible.
    """

    permission_classes = [HasPermission]
    throttle_scope = "reports"
    report_name = "report"

    def audit_export(self, request, fmt: str, row_count: int) -> None:
        if fmt == "json":
            return
        record(
            AuditAction.EXPORT,
            f"reporting.{self.report_name}",
            entity_label=self.report_name,
            new_value={
                "format": fmt,
                "rows": row_count,
                "filters": dict(request.query_params),
            },
            notes=f"Report exported: {self.report_name} ({fmt}, {row_count} rows)",
        )


class DashboardView(_ReportView):
    required_permissions = "reporting.view_dashboard"
    report_name = "dashboard"

    @extend_schema(tags=["reporting"], summary="Dashboard KPIs", responses={200: DashboardSerializer})
    def get(self, request):
        from apps.inventory.models import Warehouse

        warehouse_id = request.query_params.get("warehouse")
        warehouse = get_object_or_404(Warehouse, pk=warehouse_id) if warehouse_id else None

        kpis = services.dashboard_kpis(warehouse=warehouse)

        # Margin figures are commercially sensitive; only users with the
        # explicit permission see them.
        if not request.user.has_perm_code("sales.view_margin"):
            kpis["sales"].pop("daily_margin", None)
            kpis["sales"].pop("monthly_margin", None)
        if not request.user.has_perm_code("inventory.view_valuation"):
            kpis["inventory"].pop("total_value", None)

        return Response(kpis)


class DashboardWidgetsView(_ReportView):
    required_permissions = "reporting.view_dashboard"
    report_name = "dashboard_widgets"

    @extend_schema(
        tags=["reporting"], summary="Dashboard widget data", responses={200: DashboardWidgetsSerializer},
        parameters=[OpenApiParameter("days", int, description="Trend window, default 30")],
    )
    def get(self, request):
        days = int(request.query_params.get("days", 30))
        return Response(
            {
                "revenue_trend": services.revenue_trend(days=days),
                "top_customers": services.top_customers(days=days),
                "top_products": services.top_products(days=days),
            }
        )


class InventoryValuationReportView(_ReportView):
    required_permissions = "reporting.view_inventory_reports"
    report_name = "inventory_valuation"

    @extend_schema(
        tags=["reporting"], summary="Inventory valuation report", responses={200: ValuationReportSerializer},
        parameters=[
            OpenApiParameter("warehouse", str),
            OpenApiParameter("category", str),
            OpenApiParameter("export", str, enum=["json", "csv", "xlsx", "pdf"], description="Export format; omit for JSON"),
        ],
    )
    def get(self, request):
        from apps.catalog.models import Category
        from apps.inventory.models import Warehouse

        warehouse_id = request.query_params.get("warehouse")
        category_id = request.query_params.get("category")
        fmt = request.query_params.get("export", "json")

        report = services.inventory_valuation_report(
            warehouse=get_object_or_404(Warehouse, pk=warehouse_id) if warehouse_id else None,
            category=get_object_or_404(Category, pk=category_id) if category_id else None,
        )
        self.audit_export(request, fmt, len(report["lines"]))

        if fmt in {"csv", "xlsx", "pdf"}:
            return export(
                fmt,
                report["lines"],
                [
                    ("product_code", _("Product code")),
                    ("product_name", _("Product")),
                    ("category", _("Category")),
                    ("warehouse", _("Warehouse")),
                    ("batch_number", _("Batch")),
                    ("expiry_date", _("Expiry")),
                    ("days_to_expiry", _("Days to expiry")),
                    ("quantity", _("Quantity")),
                    ("unit_cost", _("Unit cost (BIF)")),
                    ("value", _("Value (BIF)")),
                ],
                f"valorisation-stock-{timezone.localdate():%Y%m%d}",
                title=str(_("Inventory valuation")),
                metadata={
                    str(_("Total value")): report["total_value"],
                    str(_("Batches")): report["batch_count"],
                },
            )
        return Response(report)


class ExpiryReportView(_ReportView):
    required_permissions = "reporting.view_inventory_reports"
    report_name = "expiry"

    @extend_schema(
        tags=["reporting"], summary="Expiry report", responses={200: ExpiryReportSerializer},
        parameters=[
            OpenApiParameter("days", int, description="Horizon in days, default 180"),
            OpenApiParameter("warehouse", str),
            OpenApiParameter("export", str, enum=["json", "csv", "xlsx", "pdf"], description="Export format; omit for JSON"),
        ],
    )
    def get(self, request):
        from apps.inventory.models import Warehouse

        days = int(request.query_params.get("days", 180))
        warehouse_id = request.query_params.get("warehouse")
        fmt = request.query_params.get("export", "json")

        report = services.expiry_report(
            days=days,
            warehouse=get_object_or_404(Warehouse, pk=warehouse_id) if warehouse_id else None,
        )
        self.audit_export(request, fmt, len(report["lines"]))

        if fmt in {"csv", "xlsx", "pdf"}:
            return export(
                fmt,
                report["lines"],
                [
                    ("product_code", _("Product code")),
                    ("product_name", _("Product")),
                    ("batch_number", _("Batch")),
                    ("warehouse", _("Warehouse")),
                    ("supplier", _("Supplier")),
                    ("expiry_date", _("Expiry")),
                    ("days_to_expiry", _("Days to expiry")),
                    ("quantity", _("Quantity")),
                    ("value_at_risk", _("Value at risk (BIF)")),
                ],
                f"peremption-{timezone.localdate():%Y%m%d}",
                title=str(_("Expiry report")),
                metadata={
                    str(_("Horizon (days)")): days,
                    str(_("Total value at risk")): report["total_value_at_risk"],
                },
            )
        return Response(report)


class StockMovementReportView(_ReportView):
    required_permissions = "reporting.view_inventory_reports"
    report_name = "stock_movement"

    @extend_schema(
        tags=["reporting"], summary="Stock movement report", responses={200: MovementReportSerializer},
        parameters=[
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
            OpenApiParameter("product", str),
            OpenApiParameter("warehouse", str),
            OpenApiParameter("export", str, enum=["json", "csv", "xlsx", "pdf"], description="Export format; omit for JSON"),
        ],
    )
    def get(self, request):
        fmt = request.query_params.get("export", "json")
        report = services.stock_movement_report(
            date_from=_parse_date(request.query_params.get("date_from")),
            date_to=_parse_date(request.query_params.get("date_to")),
            product=request.query_params.get("product"),
            warehouse=request.query_params.get("warehouse"),
        )
        self.audit_export(request, fmt, len(report["lines"]))

        if fmt in {"csv", "xlsx", "pdf"}:
            columns = [
                ("date", _("Date")),
                ("product_code", _("Product code")),
                ("product_name", _("Product")),
                ("batch_number", _("Batch")),
                ("warehouse", _("Warehouse")),
                ("movement_type", _("Movement")),
                ("quantity_delta", _("Quantity")),
                ("balance_after", _("Balance")),
                ("value", _("Value (BIF)")),
                ("reference", _("Reference")),
                ("performed_by", _("User")),
            ]
            # Reason is free text and runs long; at twelve columns it wrecks the
            # landscape layout. The spreadsheet exports still carry it.
            if fmt != "pdf":
                columns.append(("reason", _("Reason")))
            return export(
                fmt,
                report["lines"],
                columns,
                f"mouvements-stock-{timezone.localdate():%Y%m%d}",
                title=str(_("Stock movements")),
            )
        return Response(report)


class DeadStockReportView(_ReportView):
    required_permissions = "reporting.view_inventory_reports"
    report_name = "dead_stock"

    @extend_schema(
        tags=["reporting"], summary="Dead stock report", responses={200: DeadStockReportSerializer},
        parameters=[
            OpenApiParameter("days", int, description="Days without movement, default 180"),
            OpenApiParameter("warehouse", str),
            OpenApiParameter("export", str, enum=["json", "csv", "xlsx", "pdf"], description="Export format; omit for JSON"),
        ],
    )
    def get(self, request):
        from apps.inventory.models import Warehouse

        fmt = request.query_params.get("export", "json")
        warehouse_id = request.query_params.get("warehouse")
        report = services.dead_stock_report(
            days_without_movement=int(request.query_params.get("days", 180)),
            warehouse=get_object_or_404(Warehouse, pk=warehouse_id) if warehouse_id else None,
        )
        self.audit_export(request, fmt, len(report["lines"]))

        if fmt in {"csv", "xlsx", "pdf"}:
            return export(
                fmt,
                report["lines"],
                [
                    ("product_code", _("Product code")),
                    ("product_name", _("Product")),
                    ("batch_number", _("Batch")),
                    ("warehouse", _("Warehouse")),
                    ("quantity", _("Quantity")),
                    ("value", _("Value (BIF)")),
                    ("expiry_date", _("Expiry")),
                    ("received_at", _("Received")),
                ],
                f"stock-dormant-{timezone.localdate():%Y%m%d}",
                title=str(_("Dead stock")),
            )
        return Response(report)


class SalesReportView(_ReportView):
    required_permissions = "reporting.view_sales_reports"
    report_name = "sales"

    @extend_schema(
        tags=["reporting"], summary="Sales report", responses={200: SalesReportSerializer},
        parameters=[
            OpenApiParameter("date_from", str, required=True),
            OpenApiParameter("date_to", str, required=True),
            OpenApiParameter("group_by", str, enum=["day", "month"]),
            OpenApiParameter("customer", str),
            OpenApiParameter("salesperson", str),
            OpenApiParameter("export", str, enum=["json", "csv", "xlsx", "pdf"], description="Export format; omit for JSON"),
        ],
    )
    def get(self, request):
        fmt = request.query_params.get("export", "json")
        today = timezone.now()

        report = services.sales_report(
            date_from=_parse_date(
                request.query_params.get("date_from"), today - timezone.timedelta(days=30)
            ),
            date_to=_parse_date(request.query_params.get("date_to"), today),
            group_by=request.query_params.get("group_by", "day"),
            customer=request.query_params.get("customer"),
            user=request.query_params.get("salesperson"),
        )

        # Margin is privileged even inside a sales report.
        if not request.user.has_perm_code("sales.view_margin"):
            for line in report["lines"]:
                line.pop("cost", None)
                line.pop("margin", None)
                line.pop("margin_percent", None)
            report["totals"].pop("cost", None)
            report["totals"].pop("margin", None)

        self.audit_export(request, fmt, len(report["lines"]))

        if fmt in {"csv", "xlsx", "pdf"}:
            columns = [
                ("period", _("Period")),
                ("revenue", _("Revenue (BIF)")),
                ("tax", _("VAT (BIF)")),
                ("net_revenue", _("Net revenue (BIF)")),
                ("discount", _("Discounts (BIF)")),
                ("transactions", _("Transactions")),
            ]
            if request.user.has_perm_code("sales.view_margin"):
                columns.extend(
                    [("cost", _("Cost (BIF)")), ("margin", _("Margin (BIF)"))]
                )
            return export(
                fmt, report["lines"], columns,
                f"ventes-{timezone.localdate():%Y%m%d}",
                title=str(_("Sales report")),
            )
        return Response(report)


class ReceivablesAgeingView(_ReportView):
    required_permissions = "reporting.view_financial_reports"
    report_name = "receivables_ageing"

    @extend_schema(
        tags=["reporting"], summary="Receivables ageing", responses={200: ReceivablesAgeingSerializer},
        parameters=[
            OpenApiParameter("as_of", str),
            OpenApiParameter("export", str, enum=["json", "csv", "xlsx", "pdf"], description="Export format; omit for JSON"),
        ],
    )
    def get(self, request):
        fmt = request.query_params.get("export", "json")
        report = services.receivables_ageing(
            as_of=_parse_date(request.query_params.get("as_of"))
        )
        self.audit_export(request, fmt, len(report["customers"]))

        if fmt in {"csv", "xlsx", "pdf"}:
            return export(
                fmt,
                report["customers"],
                [
                    ("customer_code", _("Customer code")),
                    ("business_name", _("Customer")),
                    ("credit_limit", _("Credit limit (BIF)")),
                    ("current", _("Not yet due (BIF)")),
                    ("1_30", _("1–30 days (BIF)")),
                    ("31_60", _("31–60 days (BIF)")),
                    ("61_90", _("61–90 days (BIF)")),
                    ("over_90", _("Over 90 days (BIF)")),
                    ("total", _("Total (BIF)")),
                ],
                f"balance-agee-{timezone.localdate():%Y%m%d}",
                title=str(_("Receivables ageing")),
                metadata={str(_("Total outstanding")): report["total"]},
            )
        return Response(report)


class ProfitLossView(_ReportView):
    required_permissions = "reporting.view_financial_reports"
    report_name = "profit_loss"

    @extend_schema(
        tags=["reporting"],
        summary="Gross trading result",
        description=(
            "Gross margin only. Operating expenses are not tracked by this "
            "system, so this is not a statutory profit and loss account."
        ),
        responses={200: ProfitLossSerializer},
        parameters=[
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
            OpenApiParameter(
                "export", str, enum=["json", "csv", "xlsx", "pdf"],
                description="Export format; omit for JSON",
            ),
        ],
    )
    def get(self, request):
        today = timezone.now()
        date_from = _parse_date(
            request.query_params.get("date_from"), today - timezone.timedelta(days=30)
        )
        date_to = _parse_date(request.query_params.get("date_to"), today)
        report = services.profit_and_loss(date_from=date_from, date_to=date_to)

        fmt = request.query_params.get("export", "json")
        self.audit_export(request, fmt, 1)

        if fmt in {"csv", "xlsx", "pdf"}:
            # A P&L is a set of figures, not a row set. Pivoting it to
            # label/amount rows lets it share the generic exporters instead of
            # needing a bespoke writer per format.
            lines = [
                {"item": str(_("Gross revenue (BIF)")), "amount": report["gross_revenue"]},
                {"item": str(_("VAT collected (BIF)")), "amount": report["vat_collected"]},
                {"item": str(_("Net revenue (BIF)")), "amount": report["net_revenue"]},
                {"item": str(_("Cost of goods sold (BIF)")), "amount": report["cost_of_goods_sold"]},
                {"item": str(_("Gross profit (BIF)")), "amount": report["gross_profit"]},
                {"item": str(_("Gross margin (%)")), "amount": report["gross_margin_percent"]},
                {"item": str(_("Discounts granted (BIF)")), "amount": report["discounts_granted"]},
            ]
            return export(
                fmt,
                lines,
                [("item", _("Item")), ("amount", _("Amount"))],
                f"resultat-brut-{timezone.localdate():%Y%m%d}",
                title=str(_("Gross trading result")),
                metadata={
                    str(_("From")): date_from,
                    str(_("To")): date_to,
                    str(_("Note")): str(
                        _("Gross margin only; operating expenses are not tracked.")
                    ),
                },
            )
        return Response(report)


class ComplianceReportView(_ReportView):
    """
    Compliance pack: adjustments, write-offs and expired stock.

    These are the three artefacts a pharmaceutical inspection asks for, so they
    are grouped into one endpoint rather than requiring three separate pulls.
    """

    required_permissions = "reporting.view_compliance_reports"
    report_name = "compliance"

    @extend_schema(
        tags=["reporting"], summary="Compliance report pack", responses={200: ComplianceReportSerializer},
        parameters=[
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
            OpenApiParameter("export", str, enum=["json", "csv", "xlsx", "pdf"], description="Export format; omit for JSON"),
        ],
    )
    def get(self, request):
        from apps.inventory.models import MovementType, StockMovement

        date_from = _parse_date(
            request.query_params.get("date_from"),
            timezone.now() - timezone.timedelta(days=90),
        )
        date_to = _parse_date(request.query_params.get("date_to"), timezone.now())

        movements = StockMovement.objects.filter(
            performed_at__gte=date_from,
            performed_at__lte=date_to,
            movement_type__in=[
                MovementType.ADJUSTMENT_IN,
                MovementType.ADJUSTMENT_OUT,
                MovementType.DAMAGE,
                MovementType.EXPIRY,
                MovementType.DISPOSAL,
            ],
        ).select_related("product", "batch", "performed_by").order_by("-performed_at")

        lines = [
            {
                "date": m.performed_at,
                "movement_type": m.get_movement_type_display(),
                "product_code": m.product.product_code,
                "product_name": m.product.name,
                "batch_number": m.batch.batch_number,
                "expiry_date": m.batch.expiry_date,
                "quantity": abs(m.quantity_delta),
                "value": m.total_value,
                "reason": m.reason,
                "performed_by": m.performed_by.get_full_name() if m.performed_by else "",
                "reference": m.source_reference,
            }
            for m in movements.iterator(chunk_size=500)
        ]

        fmt = request.query_params.get("export", "json")
        self.audit_export(request, fmt, len(lines))

        if fmt in {"csv", "xlsx", "pdf"}:
            return export(
                fmt,
                lines,
                [
                    ("date", _("Date")),
                    ("movement_type", _("Type")),
                    ("product_code", _("Product code")),
                    ("product_name", _("Product")),
                    ("batch_number", _("Batch")),
                    ("expiry_date", _("Expiry")),
                    ("quantity", _("Quantity")),
                    ("value", _("Value (BIF)")),
                    ("reason", _("Reason")),
                    ("performed_by", _("User")),
                    ("reference", _("Reference")),
                ],
                f"conformite-{timezone.localdate():%Y%m%d}",
                title=str(_("Compliance report")),
            )

        return Response(
            {
                "date_from": date_from,
                "date_to": date_to,
                "lines": lines,
                "movement_count": len(lines),
                "generated_at": timezone.now(),
            }
        )
