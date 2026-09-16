"""
Phase 11/42 — WebsiteIntegrationProvider interface.

Domain logic (routers, sync engine) depends only on this ABC, never on a
concrete website's SDK/HTTP client. Per plan §103, the actual India Gate
website's platform/auth/API surface is UNKNOWN as of Phase 0 — that's a
real open item, not a detail being deferred for convenience. Until it's
answered, `mock.MockWebsiteProvider` is wired in via
Settings.website_provider="mock" (the default), which is what makes the
rest of the system buildable and testable with zero paid/external
services today (plan §5/§6).

When the real platform is identified, a new class implementing this same
ABC (e.g. `WooCommerceProvider`, `ShopifyProvider`, `CustomLaravelProvider`)
gets built and swapped in via config — no other module changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WebsiteOrderDTO:
    external_order_number: str
    customer_name: str | None
    customer_phone: str | None
    customer_email: str | None
    delivery_method: str
    delivery_address: str | None
    payment_status: str
    order_notes: str | None
    lines: list[dict[str, Any]]
    total_minor: int
    currency: str
    external_event_id: str  # for idempotency — see WebsiteSyncEvent


@dataclass(frozen=True, slots=True)
class WebsiteProductDTO:
    external_product_id: str
    sku: str | None
    barcode: str | None
    name: str
    description: str | None
    category_name: str | None
    price_minor: int
    currency: str
    is_active: bool


class WebsiteIntegrationProvider(ABC):
    @abstractmethod
    def get_orders(self, since: str | None = None) -> list[WebsiteOrderDTO]:
        ...

    @abstractmethod
    def get_order(self, external_order_number: str) -> WebsiteOrderDTO | None:
        ...

    @abstractmethod
    def update_order_status(self, external_order_number: str, new_status: str) -> bool:
        ...

    @abstractmethod
    def get_products(self) -> list[WebsiteProductDTO]:
        ...

    @abstractmethod
    def create_product(self, product: WebsiteProductDTO) -> str:
        """Returns the new external_product_id."""

    @abstractmethod
    def update_product(self, external_product_id: str, product: WebsiteProductDTO) -> bool:
        ...

    @abstractmethod
    def get_categories(self) -> list[str]:
        ...

    @abstractmethod
    def create_category(self, name: str) -> str:
        ...
