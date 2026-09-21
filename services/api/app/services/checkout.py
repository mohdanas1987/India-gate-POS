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
from app.services.authorization import AuthorizationError, resolve_authorized_register


class CheckoutError(Exception):
    pass


class NoOpenSessionError(CheckoutError):
    pass


class InvalidCartError(CheckoutError):
    pass


class UnauthorizedResourceError(CheckoutError):
    """
    Phase 8.5 (CTO audit of 0cfd8ca, finding #6 — RELEASE BLOCKER):
    create_pos_sale() used to trust register_id/product_id as belonging to
    the caller's tenant/store just because the route's Principal check
    passed. That is not sufficient — a Tenant A caller who guesses or
    manipulates Tenant B's register_id or product_id must be refused HERE,
    in the domain service, not merely at the route layer. This exception
    is raised whenever that chain of ownership does not hold.
    """


@dataclass(frozen=True, slots=True)
class CartLineInput:
    product_id: int
    quantity: int  # for weighted products this is grams; validated by caller/UI


@dataclass(frozen=True, slots=True)
class PaymentInput:
    """
    Phase 22 (LedgerBrug dev-team question #2 — split payments). One
    tender on a receipt: 10 EUR cash + 15 EUR by card is two of these, not
    one payment_method with a total. `provider_reference` is where the
    card network/PSP's own transaction id goes (CCV, Adyen, Rabobank,
    whichever is picked — see PHASE-STATUS.md Phase 22) so the amount that
    later lands on the bank statement can be matched back to this tender.
    """

    method: PaymentMethod
    amount_minor: int
    provider_reference: str | None = None


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


def get_open_session(db: Session, tenant_id: int, store_id: int, register_id: int) -> CashierSession:
    # Defense in depth, even after resolve_authorized_register() has
    # already proven register_id belongs to this tenant/store: also
    # require the session ROW ITSELF to carry the same tenant_id/store_id,
    # not just the same register_id. A session is a full three-part key
    # (tenant, store, register), and matching on register_id alone was
    # exactly the CTO's finding #6 — "essentially CashierSession.register_id
    # == register_id ... rather than enforcing tenant_id, store_id,
    # register_id together."
    session = (
        db.query(CashierSession)
        .filter(
            CashierSession.register_id == register_id,
            CashierSession.tenant_id == tenant_id,
            CashierSession.store_id == store_id,
            CashierSession.is_open.is_(True),
        )
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
    payment_method: PaymentMethod | None = None,
    payments: list[PaymentInput] | None = None,
    currency: str = "EUR",
) -> Order:
    """
    NOTE on transaction ownership (CTO audit of 0cfd8ca, finding #17 —
    "the service shouldn't independently commit"): this function no
    longer calls db.commit() itself. It flushes so generated ids (order.id,
    etc.) are available to build dependent rows within the same call, but
    the CALLER (the route, or the sync endpoint) owns the transaction
    boundary — it decides when to commit and is responsible for rolling
    back on any exception this function raises. This lets a caller compose
    this operation with other writes (an inbox event, an audit trail
    entry outside this function's own AuditLog row, a website-order status
    update) inside exactly one atomic transaction, instead of two services
    each independently committing and stepping on each other.

    Payment: pass EITHER `payment_method` (single tender for the full
    total — the pre-Phase-22 behavior, kept so every existing caller and
    test keeps working unchanged) OR `payments` (a list of PaymentInput
    tenders — Phase 22, split payments). Exactly one must be given; the
    split-payment path validates the tenders sum to exactly the computed
    total before writing anything, so a client cannot under- or
    over-tender a sale by mistake.
    """
    if not lines:
        raise InvalidCartError("Cart is empty")
    if (payment_method is None) == (payments is None):
        raise InvalidCartError("Pass exactly one of payment_method or payments, not both or neither")

    try:
        resolve_authorized_register(db, tenant_id, store_id, register_id)
    except AuthorizationError as exc:
        raise UnauthorizedResourceError(str(exc)) from exc

    session = get_open_session(db, tenant_id, store_id, register_id)

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
        # CTO audit of 0cfd8ca, finding #6 (RELEASE BLOCKER): this used to
        # be db.get(Product, line_input.product_id) with NO tenant filter
        # at all — a Tenant A cashier who knew or guessed a Tenant B
        # product id could ring up a sale against another tenant's catalog
        # entirely undetected. Every product must now prove it belongs to
        # the same tenant transacting, not just exist somewhere in the DB.
        product = (
            db.query(Product)
            .filter(Product.id == line_input.product_id, Product.tenant_id == tenant_id)
            .first()
        )
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
                # Phase 22: snapshot the RATE, not just the resulting
                # amount — see the field's docstring in app/domain/orders.py.
                tax_rate_basis_points=tax.rate_basis_points if tax else 0,
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

    # Phase 22 (dev-team question #3): a permanent, unique, barcode-ready
    # receipt number, assigned once the order id (and therefore the
    # number derived from it) is known and never changed afterward —
    # including through a later void or refund, per the dev team's
    # explicit requirement that a void keep its original receipt number.
    order.receipt_number = f"{store_id:04d}-{register_id:03d}-{order.id:010d}"

    if payments is not None:
        if not payments:
            raise InvalidCartError("At least one payment tender is required")
        tendered_minor = sum(p.amount_minor for p in payments)
        if tendered_minor != total_minor:
            raise InvalidCartError(
                f"Payment tenders sum to {tendered_minor} but the order total is {total_minor}"
            )
        for tender in payments:
            if tender.amount_minor <= 0:
                raise InvalidCartError("Each payment tender must be a positive amount")
            db.add(
                OrderPayment(
                    order_id=order.id,
                    method=tender.method,
                    amount_minor=tender.amount_minor,
                    provider_reference=tender.provider_reference,
                )
            )
    else:
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

    # Flush, don't commit — see the transaction-ownership note on this
    # function's docstring. The caller commits (or rolls back).
    db.flush()
    return order
