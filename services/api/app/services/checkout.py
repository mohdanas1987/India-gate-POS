"""
Phase 6/7 — Checkout: the piece flagged in the phase-status report as the
single largest missing piece. This is what turns the Order/OrderLine/
InventoryLedger *schema* (already migrated, Phase 6/7 "model only") into
an actual usable sale.

Design choices, stated explicitly so they can be revisited:
  - A sale can only be created while the cashier has an OPEN
    CashierSession on that register (plan's cash-register/session model,
    Phase 0 audit found the legacy app enforced this implicitly via
    `/pos/opening-day-cash-amount` — here it's an explicit precondition,
    checked and tested).
  - Every line's tax is taken from the product's linked Tax at the moment
    of sale (a historical snapshot in `OrderLine.tax_minor`), not
    recomputed later if the tax rate changes — orders must not silently
    reprice themselves.
  - One InventoryLedger SALE row per order line, with quantity_delta
    negative. This is the first thing that actually writes to that ledger
    (Phase 6 was schema-only before this).
  - This function does NOT decide the website-order inventory reservation
    policy question (plan §46) — that's for WEBSITE-channel orders,
    unresolved pending business-rule input; POS-channel sales always
    decrement immediately, which is unambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.money import Money
from app.domain.authz import AuditLog
from app.domain.cash import CashierSession
from app.domain.catalog import Product, Tax
from app.domain.inventory import InventoryLedger, LedgerEventType
from app.domain.orders import Order, OrderLine, OrderPayment, OrderStatus, PaymentMethod, SalesChannel


class CheckoutError(Exception):
    pass


class NoOpenSessionError(CheckoutError):
    pass


class InvalidCartError(CheckoutError):
    pass


@dataclass(frozen=True, slots=True)
class CartLineInput:
    product_id: int
    quantity: int  # for weighted products this is grams; validated by caller/UI


@dataclass(frozen=True, slots=True)
class OrderTotals:
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    currency: str


def compute_line_total(product: Product, tax: Tax | None, quantity: int) -> tuple[int, int, int]:
    """Returns (line_subtotal_minor, line_tax_minor, line_total_minor)."""
    if quantity <= 0:
        raise InvalidCartError(f"Product {product.id} has non-positive quantity {quantity}")
    unit_price = Money(product.price_minor, product.currency)
    line_subtotal = unit_price * quantity
    line_tax = line_subtotal.percentage(tax.rate_basis_points / 100) if tax else Money(0, product.currency)
    return line_subtotal.minor_units, line_tax.minor_units, (line_subtotal + line_tax).minor_units


def get_open_session(db: Session, register_id: int) -> CashierSession:
    session = (
        db.query(CashierSession)
        .filter(CashierSession.register_id == register_id, CashierSession.is_open.is_(True))
        .order_by(CashierSession.id.desc())
        .first()
    )
    if not session:
        raise NoOpenSessionError(
            f"No open cashier session on register {register_id} — open a session before ringing up a sale"
        )
    return session


def create_pos_sale(
    db: Session,
    tenant_id: int,
    store_id: int,
    register_id: int,
    cashier_user_id: int,
    lines: list[CartLineInput],
    payment_method: PaymentMethod,
    currency: str = "EUR",
) -> Order:
    if not lines:
        raise InvalidCartError("Cart is empty")

    session = get_open_session(db, register_id)

    order = Order(
        tenant_id=tenant_id,
        store_id=store_id,
        register_id=register_id,
        cashier_user_id=cashier_user_id,
        sales_channel=SalesChannel.POS,
        status=OrderStatus.OPEN,
        currency=currency,
    )
    db.add(order)
    db.flush()

    subtotal_minor = 0
    tax_minor = 0

    for line_input in lines:
        product = db.get(Product, line_input.product_id)
        if not product or product.is_deleted:
            raise InvalidCartError(f"Product {line_input.product_id} not found or deleted")

        tax = db.get(Tax, product.tax_id) if product.tax_id else None
        line_sub, line_tax, line_total = compute_line_total(product, tax, line_input.quantity)

        db.add(
            OrderLine(
                order_id=order.id,
                product_id=product.id,
                quantity=line_input.quantity,
                unit_price_minor=product.price_minor,
                tax_minor=line_tax,
                line_total_minor=line_total,
            )
        )
        db.add(
            InventoryLedger(
                tenant_id=tenant_id,
                store_id=store_id,
                product_id=product.id,
                event_type=LedgerEventType.SALE,
                quantity_delta=-line_input.quantity,
                reference_type="order",
                reference_id=str(order.id),
            )
        )
        subtotal_minor += line_sub
        tax_minor += line_tax

    total_minor = subtotal_minor + tax_minor
    order.subtotal_minor = subtotal_minor
    order.tax_minor = tax_minor
    order.discount_minor = 0
    order.total_minor = total_minor
    order.status = OrderStatus.COMPLETED

    db.add(OrderPayment(order_id=order.id, method=payment_method, amount_minor=total_minor))
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=cashier_user_id,
            action="order.create",
            entity_type="order",
            entity_id=str(order.id),
            after={"total_minor": total_minor, "currency": currency, "line_count": len(lines)},
            source="POS",
        )
    )

    db.commit()
    db.refresh(order)
    return order
