from django.urls import path

from .api import (
    ComplianceReportView,
    DashboardView,
    DashboardWidgetsView,
    DeadStockReportView,
    ExpiryReportView,
    InventoryValuationReportView,
    ProfitLossView,
    ReceivablesAgeingView,
    SalesReportView,
    StockMovementReportView,
)

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("dashboard/widgets/", DashboardWidgetsView.as_view(), name="dashboard-widgets"),
    # Inventory
    path("inventory/valuation/", InventoryValuationReportView.as_view(), name="report-valuation"),
    path("inventory/expiry/", ExpiryReportView.as_view(), name="report-expiry"),
    path("inventory/movements/", StockMovementReportView.as_view(), name="report-movements"),
    path("inventory/dead-stock/", DeadStockReportView.as_view(), name="report-dead-stock"),
    # Sales & financial
    path("sales/", SalesReportView.as_view(), name="report-sales"),
    path("financial/receivables-ageing/", ReceivablesAgeingView.as_view(), name="report-ageing"),
    path("financial/profit-loss/", ProfitLossView.as_view(), name="report-profit-loss"),
    # Compliance
    path("compliance/", ComplianceReportView.as_view(), name="report-compliance"),
]
