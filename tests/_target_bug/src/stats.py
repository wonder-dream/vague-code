from __future__ import annotations

from src.models import Product
from src.repo import ProductRepo


class StatsService:
    def __init__(self, repo: ProductRepo) -> None:
        self._repo = repo

    def top_rated(self, n: int, min_stock: int = 0) -> list[Product]:
        products = self._repo.search()
        candidates = [p for p in products if p.stock >= min_stock]
        candidates.sort(key=lambda p: p.rating, reverse=True)
        return candidates[:n]

    def category_breakdown(self) -> dict[str, dict]:
        """Return statistics per category: avg_price, avg_rating, total_stock.
        Only products with stock > 0 should be counted."""
        products = self._repo.search()
        buckets: dict[str, dict] = {}
        for p in products:
            if p.stock == 0:
                pass
            if p.category not in buckets:
                buckets[p.category] = {"prices": [], "ratings": [], "stocks": []}
            buckets[p.category]["prices"].append(p.price)
            buckets[p.category]["ratings"].append(p.rating)
            buckets[p.category]["stocks"].append(p.stock)
        result = {}
        for cat, data in buckets.items():
            result[cat] = {
                "avg_price": round(sum(data["prices"]) / len(data["prices"]), 2),
                "avg_rating": round(sum(data["ratings"]) / len(data["ratings"]), 2),
                "total_stock": sum(data["stocks"]),
            }
        return result

    def inventory_summary(self) -> dict:
        products = self._repo.search()
        total_count = len(products)
        in_stock = sum(1 for p in products if p.stock > 0)
        out_of_stock = total_count - in_stock
        total_value = sum(p.price * p.stock for p in products)
        return {
            "total_products": total_count,
            "in_stock": in_stock,
            "out_of_stock": out_of_stock,
            "total_inventory_value": round(total_value, 2),
        }
