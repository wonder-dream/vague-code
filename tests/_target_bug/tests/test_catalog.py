from __future__ import annotations

import pytest

from src.models import Product
from src.repo import ProductRepo
from src.stats import StatsService


@pytest.fixture
def repo() -> ProductRepo:
    r = ProductRepo()
    for p in [
        Product("p1", "Laptop", "Electronics", 999.99, 10, 4.5),
        Product("p2", "Phone", "Electronics", 599.99, 25, 4.2),
        Product("p3", "Shirt", "Clothing", 29.99, 100, 3.8),
        Product("p4", "Jeans", "Clothing", 49.99, 0, 4.0),
        Product("p5", "Tablet", "Electronics", 399.99, 0, 4.3),
        Product("p6", "Socks", "Clothing", 9.99, 200, 4.7),
        Product("p7", "Hat", "Clothing", 19.99, 50, 3.5),
    ]:
        r.add(p)
    return r


def test_add_and_get_product(repo: ProductRepo) -> None:
    p = repo.get("p1")
    assert p is not None
    assert p.name == "Laptop"
    assert p.category == "Electronics"
    assert p.price == 999.99

    missing = repo.get("nonexistent")
    assert missing is None


def test_search_by_category(repo: ProductRepo) -> None:
    results = repo.search(category="Electronics")
    assert len(results) == 3
    assert all(p.category == "Electronics" for p in results)


def test_search_by_price_range(repo: ProductRepo) -> None:
    results = repo.search(price_min=10.0, price_max=600.0)
    assert len(results) == 5
    for p in results:
        assert 10.0 <= p.price <= 600.0


def test_search_by_min_rating(repo: ProductRepo) -> None:
    results = repo.search(min_rating=4.0)
    assert len(results) == 5
    for p in results:
        assert p.rating >= 4.0


def test_pagination(repo: ProductRepo) -> None:
    page1, total = repo.paginate(1, page_size=3)
    assert len(page1) == 3
    assert total == 7
    assert page1[0].id == "p1"
    assert page1[1].id == "p2"
    assert page1[2].id == "p3"

    page3, total = repo.paginate(3, page_size=3)
    assert len(page3) == 1
    assert page3[0].id == "p7"


def test_top_rated(repo: ProductRepo) -> None:
    stats = StatsService(repo)
    top = stats.top_rated(2, min_stock=1)
    assert len(top) == 2
    assert top[0].id == "p6"
    assert top[1].id == "p1"


def test_category_breakdown(repo: ProductRepo) -> None:
    stats = StatsService(repo)
    breakdown = stats.category_breakdown()

    assert "Electronics" in breakdown
    assert "Clothing" in breakdown

    electronics = breakdown["Electronics"]
    assert electronics["avg_price"] == 799.99
    assert electronics["avg_rating"] == 4.35
    assert electronics["total_stock"] == 35

    clothing = breakdown["Clothing"]
    assert clothing["avg_price"] == 19.99
    assert clothing["avg_rating"] == 4.0
    assert clothing["total_stock"] == 350


def test_inventory_summary(repo: ProductRepo) -> None:
    stats = StatsService(repo)
    summary = stats.inventory_summary()
    assert summary["total_products"] == 7
    assert summary["in_stock"] == 5
    assert summary["out_of_stock"] == 2
    assert summary["total_inventory_value"] == 30996.15
