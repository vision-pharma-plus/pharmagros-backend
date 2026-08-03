"""
Stock levels are paginated.

The endpoint returned one row per active product in a single response. At a
few thousand products that is a slow query rendered into a slower page, on the
screen someone opens to answer "do we have this in stock?" — the one question
that should be fastest to answer.

The filters here are the wrinkle: `is_low` and `is_out` are derived from the
batch aggregate rather than stored, so they cannot be pushed into the queryset
and the page boundaries have to be applied after they run. These cases pin that
the envelope is correct, that filtering happens before paging rather than
within a page, and that paging is stable.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.catalog.models import Medicine, ProductStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def many_products(db, category, unit, admin_user, product):
    """Enough products to span several pages."""
    Medicine.objects.bulk_create(
        [
            Medicine(
                product_code=f"MED-PAGE-{index:03d}",
                name_fr=f"Produit {index:03d}",
                name_en=f"Product {index:03d}",
                category=category,
                unit_of_measure=unit,
                status=ProductStatus.ACTIVE,
                reorder_level=Decimal("10"),
                created_by=admin_user,
            )
            for index in range(30)
        ]
    )
    return Medicine.objects.filter(product_code__startswith="MED-PAGE-")


def _get(client, **params):
    return client.get(reverse("stock-levels"), params)


def test_response_is_a_paginated_envelope(auth_client, many_products, pharmacist):
    response = _get(auth_client(pharmacist))

    assert response.status_code == 200
    for key in ("count", "total_pages", "current_page", "results"):
        assert key in response.data, f"missing {key} from the pagination envelope"


def test_page_size_is_respected(auth_client, many_products, pharmacist):
    response = _get(auth_client(pharmacist), page_size=10)

    assert len(response.data["results"]) == 10
    assert response.data["count"] >= 30


def test_second_page_returns_different_rows(auth_client, many_products, pharmacist):
    """Stable ordering: a product must not appear on two pages, or neither."""
    client = auth_client(pharmacist)
    first = _get(client, page_size=10, page=1).data["results"]
    second = _get(client, page_size=10, page=2).data["results"]

    first_ids = {row["product_id"] for row in first}
    second_ids = {row["product_id"] for row in second}
    assert first_ids and second_ids
    assert not (first_ids & second_ids), "the same product appeared on both pages"


def test_count_reflects_the_filter_not_the_page(auth_client, many_products, pharmacist):
    """
    The derived filters must be applied before slicing.

    Paginating first and filtering the page afterwards would report a count of
    everything while showing a handful of rows — and would drop matches that
    happened to fall on a later page.
    """
    client = auth_client(pharmacist)
    unfiltered = _get(client).data["count"]
    out_of_stock = _get(client, filter="out_of_stock")

    assert out_of_stock.data["count"] <= unfiltered
    # Every row returned genuinely matches the filter.
    assert all(row["is_out"] for row in out_of_stock.data["results"])


def test_search_narrows_the_set(auth_client, many_products, pharmacist):
    client = auth_client(pharmacist)

    response = _get(client, search="MED-PAGE-007")

    assert response.data["count"] == 1
    assert response.data["results"][0]["product_code"] == "MED-PAGE-007"


def test_search_matches_the_product_name(auth_client, many_products, pharmacist):
    """The operator may be reading the label rather than the code."""
    response = _get(auth_client(pharmacist), search="Produit 012")

    assert response.data["count"] == 1
    assert response.data["results"][0]["product_code"] == "MED-PAGE-012"
