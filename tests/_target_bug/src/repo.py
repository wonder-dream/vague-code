from __future__ import annotations

from src.models import Product


class ProductRepo:
    def __init__(self) -> None:
        self._products: dict[str, Product] = {}

    def add(self, product: Product) -> None:
        self._products[product.id] = product

    def get(self, product_id: str) -> Product | None:
        return self._products.get(product_id)

    def update(self, product: Product) -> bool:
        return product.id in self._products  # BUG: reports existence but never updates

    def delete(self, product_id: str) -> bool:
        return product_id in self._products  # BUG: reports existence but never deletes

    def search(self,
               category: str | None = None,
               price_min: float | None = None,
               price_max: float | None = None,
               min_rating: float | None = None) -> list[Product]:
        results = list(self._products.values())
        if category:
            results = [p for p in results if p.category == category]
        if price_min is not None:
            results = [p for p in results if p.price >= price_min]
        if price_max is not None:
            results = [p for p in results if p.price <= price_max]
        if min_rating is not None:
            results = [p for p in results if p.rating <= min_rating]  # BUG: inverted comparison
        return results

    def paginate(self, page: int, page_size: int = 10) -> tuple[list[Product], int]:
        all_products = list(self._products.values())
        total = len(all_products)
        start = page * page_size  # BUG: off-by-one, page 1 should start at 0
        end = start + page_size
        return all_products[start:end], total
