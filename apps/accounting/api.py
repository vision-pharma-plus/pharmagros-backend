"""
Accounting endpoints: expenses, and the reports built over them.

The report views mirror `apps.reporting` rather than living inside it: these
read supplier payments and expenses, which are accounting concerns, while
`reporting` is built around stock and sales. Keeping them here means the
financial surface is permissioned as one thing.
"""

from __future__ import annotations

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasPermission

from . import services
from .models import Expense, ExpenseCategory, ExpenseStatus
from .serializers import (
    CashOutflowSerializer,
    ExpenseCategoryReportSerializer,
    ExpenseCategorySerializer,
    ExpenseListSerializer,
    ExpenseSerializer,
    ExpenseUpdateSerializer,
    ExpenseWriteSerializer,
    FinancialOverviewSerializer,
    MarkPaidSerializer,
    OptionalReasonSerializer,
    OutstandingBalancesSerializer,
    ReasonSerializer,
    SupplierPaymentReportSerializer,
)


def _parse_date(value, default=None):
    """Parse a query-string date, falling back when absent or malformed."""
    if not value:
        return default
    from django.utils.dateparse import parse_date

    return parse_date(value) or default


def _default_window() -> tuple:
    """
    The current month to date.

    The window every financial view opens on: it is the period a manager is
    actually asking about when they open the page without choosing dates.
    """
    today = timezone.localdate()
    return today.replace(day=1), today


class ExpenseCategoryViewSet(viewsets.ModelViewSet):
    """
    Expense categories.

    Deletion is soft (BaseModel) and PROTECTed by the expenses referencing a
    category, so a heading with history behind it cannot vanish and orphan the
    reports that group by it. Retiring one is done with `is_active`.
    """

    queryset = ExpenseCategory.objects.filter(deleted_at__isnull=True)
    serializer_class = ExpenseCategorySerializer
    filterset_fields = ["is_active"]
    # Searching and ordering hit the columns, not the resolved `name` property:
    # both languages are searchable, which is what a bilingual team needs.
    search_fields = ["code", "name_fr", "name_en"]
    ordering_fields = ["name_fr", "code"]
    ordering = ["name_fr"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "accounting.view_expense",
        "retrieve": "accounting.view_expense",
        "create": "accounting.manage_expense_categories",
        "update": "accounting.manage_expense_categories",
        "partial_update": "accounting.manage_expense_categories",
        "destroy": "accounting.manage_expense_categories",
    }

    def get_queryset(self):
        # The count drives the "in use" hint on the categories screen, which is
        # what tells an administrator whether retiring one will affect history.
        # Cancelled expenses are excluded: they are not live usage.
        return super().get_queryset().annotate(
            expense_count=Count(
                "expenses",
                filter=Q(expenses__deleted_at__isnull=True)
                & ~Q(expenses__status=ExpenseStatus.CANCELLED),
            )
        )

    def perform_destroy(self, instance):
        instance.delete(actor=self.request.user)


class ExpenseFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="expense_date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="expense_date", lookup_expr="lte")
    paid_from = filters.DateFilter(field_name="paid_date", lookup_expr="gte")
    paid_to = filters.DateFilter(field_name="paid_date", lookup_expr="lte")
    unpaid = filters.BooleanFilter(method="filter_unpaid")
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = Expense
        fields = ["status", "category", "payment_method", "supplier", "purchase_order"]

    def filter_unpaid(self, queryset, name, value):
        """Costs incurred but not yet settled — what is still to pay."""
        open_statuses = [ExpenseStatus.DRAFT, ExpenseStatus.RECORDED, ExpenseStatus.APPROVED]
        if value:
            return queryset.filter(status__in=open_statuses)
        return queryset.exclude(status__in=open_statuses)

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(reference__icontains=value)
            | Q(description__icontains=value)
            | Q(payee__icontains=value)
            | Q(payment_reference__icontains=value)
            | Q(receipt_number__icontains=value)
        )


@extend_schema_view(
    list=extend_schema(tags=["accounting"], summary="List expenses"),
    retrieve=extend_schema(tags=["accounting"], summary="Retrieve an expense"),
    create=extend_schema(tags=["accounting"], summary="Record an expense"),
    partial_update=extend_schema(tags=["accounting"], summary="Amend an expense"),
    destroy=extend_schema(tags=["accounting"], summary="Delete an expense"),
)
class ExpenseViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Business costs: rent, salaries, utilities, shipping and the rest."""

    queryset = Expense.objects.filter(deleted_at__isnull=True).select_related(
        "category", "supplier", "purchase_order", "recorded_by", "approved_by"
    )
    filterset_class = ExpenseFilter
    search_fields = ["reference", "description", "payee"]
    ordering_fields = ["expense_date", "paid_date", "amount", "reference"]
    ordering = ["-expense_date"]
    permission_classes = [HasPermission]
    required_permissions = {
        "list": "accounting.view_expense",
        "retrieve": "accounting.view_expense",
        "create": "accounting.add_expense",
        "update": "accounting.change_expense",
        "partial_update": "accounting.change_expense",
        "destroy": "accounting.delete_expense",
        "approve": "accounting.approve_expense",
        "mark_paid": "accounting.change_expense",
        "cancel": "accounting.change_expense",
    }

    def get_serializer_class(self):
        if self.action == "list":
            return ExpenseListSerializer
        if self.action == "create":
            return ExpenseWriteSerializer
        if self.action in {"update", "partial_update"}:
            return ExpenseUpdateSerializer
        return ExpenseSerializer

    def _resolve_relations(self, data: dict) -> dict:
        """Swap the UUIDs in a validated payload for the objects they name."""
        from apps.partners.models import Supplier
        from apps.purchasing.models import PurchaseOrder

        if data.get("category"):
            data["category"] = get_object_or_404(ExpenseCategory, pk=data["category"])
        if data.get("supplier"):
            data["supplier"] = get_object_or_404(Supplier, pk=data["supplier"])
        elif "supplier" in data:
            data["supplier"] = None
        if data.get("purchase_order"):
            data["purchase_order"] = get_object_or_404(
                PurchaseOrder, pk=data["purchase_order"]
            )
        elif "purchase_order" in data:
            data["purchase_order"] = None
        return data

    def create(self, request, *args, **kwargs):
        serializer = ExpenseWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = self._resolve_relations(dict(serializer.validated_data))

        expense = services.create_expense(actor=request.user, **data)
        return Response(ExpenseSerializer(expense).data, status=201)

    def update(self, request, *args, **kwargs):
        """
        Amend an expense.

        PUT and PATCH behave identically — both partial — for the same reason
        as on a sale: clients send the fields they changed, and blanking the
        rest would silently discard the notes and references on every edit.
        """
        expense = self.get_object()
        serializer = ExpenseUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = self._resolve_relations(dict(serializer.validated_data))

        expense = services.update_expense(expense, actor=request.user, **data)
        return Response(ExpenseSerializer(expense).data)

    def destroy(self, request, *args, **kwargs):
        expense = self.get_object()
        serializer = OptionalReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.delete_expense(
            expense, reason=serializer.validated_data.get("reason", ""), actor=request.user
        )
        return Response(status=204)

    @extend_schema(tags=["accounting"], summary="Approve an expense", request=None)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        expense = self.get_object()
        services.approve_expense(expense, actor=request.user)
        return Response(ExpenseSerializer(expense).data)

    @extend_schema(
        tags=["accounting"], summary="Mark an expense as paid", request=MarkPaidSerializer,
    )
    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        expense = self.get_object()
        serializer = MarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.mark_expense_paid(
            expense, actor=request.user, **serializer.validated_data
        )
        return Response(ExpenseSerializer(expense).data)

    @extend_schema(
        tags=["accounting"], summary="Cancel an expense", request=ReasonSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        expense = self.get_object()
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        services.cancel_expense(
            expense, reason=serializer.validated_data["reason"], actor=request.user
        )
        return Response(ExpenseSerializer(expense).data)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


class _AccountingReportView(APIView):
    """Base for the accounting reports. Read-only, permissioned as a group."""

    permission_classes = [HasPermission]
    throttle_scope = "reports"

    def window(self, request):
        """The requested period, defaulting to the current month to date."""
        default_from, default_to = _default_window()
        return (
            _parse_date(request.query_params.get("date_from"), default_from),
            _parse_date(request.query_params.get("date_to"), default_to),
        )


DATE_PARAMS = [
    OpenApiParameter("date_from", str, description="Start of the period (YYYY-MM-DD)."),
    OpenApiParameter("date_to", str, description="End of the period, inclusive."),
]


class FinancialOverviewView(_AccountingReportView):
    """Revenue, cost of goods, expenses and the operating result for a period."""

    required_permissions = "accounting.view_financial_overview"

    @extend_schema(
        tags=["accounting"], summary="Financial overview",
        parameters=DATE_PARAMS, responses={200: FinancialOverviewSerializer},
    )
    def get(self, request):
        date_from, date_to = self.window(request)
        return Response(
            services.financial_overview(date_from=date_from, date_to=date_to)
        )


class ExpenseCategoryReportView(_AccountingReportView):
    """Expense report grouped by category."""

    required_permissions = "accounting.view_reports"

    @extend_schema(
        tags=["accounting"], summary="Expense report by category",
        parameters=DATE_PARAMS, responses={200: ExpenseCategoryReportSerializer},
    )
    def get(self, request):
        date_from, date_to = self.window(request)
        return Response(
            services.expenses_by_category(date_from=date_from, date_to=date_to)
        )


class SupplierPaymentReportView(_AccountingReportView):
    """What was paid to suppliers in a period, by supplier and by method."""

    required_permissions = "accounting.view_reports"

    @extend_schema(
        tags=["accounting"], summary="Supplier payment report",
        parameters=[*DATE_PARAMS, OpenApiParameter("supplier", str)],
        responses={200: SupplierPaymentReportSerializer},
    )
    def get(self, request):
        from apps.partners.models import Supplier

        date_from, date_to = self.window(request)

        supplier = None
        supplier_id = request.query_params.get("supplier")
        if supplier_id:
            supplier = get_object_or_404(Supplier, pk=supplier_id)

        return Response(
            services.supplier_payment_report(
                date_from=date_from, date_to=date_to, supplier=supplier
            )
        )


class OutstandingBalancesReportView(_AccountingReportView):
    """
    What is still owed to suppliers, aged by how late it is.

    Takes `as_of` rather than a window: an outstanding balance is a position at
    a point in time, not an activity over a period.
    """

    required_permissions = "accounting.view_reports"

    @extend_schema(
        tags=["accounting"], summary="Outstanding supplier balances",
        parameters=[
            OpenApiParameter(
                "as_of", str, description="Position date (YYYY-MM-DD). Defaults to today.",
            )
        ],
        responses={200: OutstandingBalancesSerializer},
    )
    def get(self, request):
        as_of = _parse_date(request.query_params.get("as_of"), timezone.localdate())
        return Response(services.outstanding_supplier_balances(as_of=as_of))


class CashOutflowReportView(_AccountingReportView):
    """Everything that left the bank in a period: supplier payments plus expenses."""

    required_permissions = "accounting.view_reports"

    @extend_schema(
        tags=["accounting"], summary="Cash outflow for a period",
        parameters=DATE_PARAMS, responses={200: CashOutflowSerializer},
    )
    def get(self, request):
        date_from, date_to = self.window(request)
        return Response(services.cash_outflow(date_from=date_from, date_to=date_to))
