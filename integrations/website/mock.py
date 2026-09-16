"""
In-memory mock website — used for local dev/UAT-prep testing with zero
external services (plan §5). Deterministic, seedable, no network calls.
"""
from __future__ import annotations

import itertools

from integrations.website.base import (
    WebsiteIntegrationProvider,
    WebsiteOrderDTO,
    WebsiteProductDTO,
)


class MockWebsiteProvider(WebsiteIntegrationProvider):
    def __init__(self):
        self._orders: dict[str, WebsiteOrderDTO] = {}
        self._products: dict[str, WebsiteProductDTO] = {}
        self._categories: set[str] = {"Grocery", "Beverages", "Snacks"}
        self._order_status: dict[str, str] = {}
        self._id_seq = itertools.count(1)

    # --- test/dev seeding helpers (not part of the ABC) ---
    def seed_order(self, order: WebsiteOrderDTO) -> None:
        self._orders[order.external_order_number] = order
        self._order_status[order.external_order_number] = "NEW"

    def seed_product(self, product: WebsiteProductDTO) -> None:
        self._products[product.external_product_id] = product

    # --- ABC implementation ---
    def get_orders(self, since: str | None = None) -> list[WebsiteOrderDTO]:
        return list(self._orders.values())

    def get_order(self, external_order_number: str) -> WebsiteOrderDTO | None:
        return self._orders.get(external_order_number)

    def update_order_status(self, external_order_number: str, new_status: str) -> bool:
        if external_order_number not in self._orders:
            return False
        self._order_status[external_order_number] = new_status
        return True

    def get_products(self) -> list[WebsiteProductDTO]:
        return list(self._products.values())

    def create_product(self, product: WebsiteProductDTO) -> str:
        new_id = f"web-{next(self._id_seq)}"
        self._products[new_id] = product
        return new_id

    def update_product(self, external_product_id: str, product: WebsiteProductDTO) -> bool:
        if external_product_id not in self._products:
            return False
        self._products[external_product_id] = product
        return True

    def get_categories(self) -> list[str]:
        return sorted(self._categories)

    def create_category(self, name: str) -> str:
        self._categories.add(name)
        return name
