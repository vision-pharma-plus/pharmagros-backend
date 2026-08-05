from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import (
    CashOutflowReportView,
    ExpenseCategoryReportView,
    ExpenseCategoryViewSet,
    ExpenseViewSet,
    FinancialOverviewView,
    OutstandingBalancesReportView,
    SupplierPaymentReportView,
)

router = DefaultRouter()
router.register("expenses", ExpenseViewSet, basename="expense")
router.register("expense-categories", ExpenseCategoryViewSet, basename="expense-category")

urlpatterns = [
    path("", include(router.urls)),
    path("overview/", FinancialOverviewView.as_view(), name="financial-overview"),
    # Reports
    path(
        "reports/expenses-by-category/",
        ExpenseCategoryReportView.as_view(),
        name="report-expenses-by-category",
    ),
    path(
        "reports/supplier-payments/",
        SupplierPaymentReportView.as_view(),
        name="report-supplier-payments",
    ),
    path(
        "reports/outstanding-balances/",
        OutstandingBalancesReportView.as_view(),
        name="report-outstanding-balances",
    ),
    path(
        "reports/cash-outflow/",
        CashOutflowReportView.as_view(),
        name="report-cash-outflow",
    ),
]
