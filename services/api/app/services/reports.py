"""
Phase 22 — daily X/Z report (LedgerBrug dev-team question #5).

Scope decision, stated explicitly: this reads POS-channel orders only
(sales_channel == POS). A Z-report / kasafsluiting is a physical
register's closing reconciliation — a website order was never rung
through a till and doesn't belong in "counted cash vs. till total". If
website-channel totals need to feed LedgerBrug too, that's a separate
report, not a reason to blend the two here.

X vs Z is the `finalize` flag, not two code paths that could drift apart:
  - X-report (finalize=False): computed on demand, returned, never
    written to the database. Can be called any number of times mid-shift.
  - Z-report (finalize=True): the exact same computation, persisted as
    exactly one DayClose row per (tenant, store, business_date). Calling
    it twice for the same day is refused — see DayCloseAlreadyFinalizedError
    — because a second "final" close is precisely the kind of silent
    double-booking this report exists to prevent LedgerBrug from seeing.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain.cash import DayClose
from app.domain.orders import Order, OrderLine, OrderPayment, OrderStatus, Refund, SalesChannel


class DayCloseError(Exception):
    pass


class DayCloseAlreadyFinalizedError(DayCloseError):
    pass


@dataclass
class DayCloseResult:
    tenant_id: int
    store_id: int
    business_date: dt.date
    is_finalized: bool
    total_sales_minor: int
    total_tax_minor: int
    total_refunds_minor: int
    total_voided_minor: int
    voided_count: int
    receipt_count: int
    first_receipt_number: str | None
    last_receipt_number: str | None
    vat_breakdown: list[dict] = field(default_factory=list)
    payment_breakdown: list[dict] = field(default_factory=list)


def _completed_orders_query(db: Session, tenant_id: int, store_id: int, business_date: dt.date):
    return db.query(Order).filter(
        Order.tenant_id == tenant_id,
        Order.store_id == store_id,
        Order.sales_channel == SalesChannel.POS,
        func.date(Order.created_at) == business_date,
        Order.status.in_(
            [OrderStatus.COMPLETED, OrderStatus.REFUNDED, OrderStatus.PARTIALLY_REFUNDED]
        ),
    )


def build_day_close(
    db: Session,
    tenant_id: int,
    store_id: int,
    business_date: dt.date,
    finalize: bool = False,
    closed_by_user_id: int | None = None,
) -> DayCloseResult:
    if finalize:
        existing = (
            db.query(DayClose)
            .filter(
                DayClose.tenant_id == tenant_id,
                DayClose.store_id == store_id,
                DayClose.business_date == business_date,
                DayClose.is_finalized.is_(True),
            )
            .first()
        )
        if existing:
            raise DayCloseAlreadyFinalizedError(
                f"Store {store_id} already has a finalized Z-report for {business_date}"
            )

    orders = _completed_orders_query(db, tenant_id, store_id, business_date).order_by(Order.id).all()
    order_ids = [o.id for o in orders]

    total_sales_minor = sum(o.total_minor for o in orders)
    total_tax_minor = sum(o.tax_minor for o in orders)
    receipt_count = len(orders)
    receipt_numbers = [o.receipt_number for o in orders if o.receipt_number]
    first_receipt_number = min(receipt_numbers) if receipt_numbers else None
    last_receipt_number = max(receipt_numbers) if receipt_numbers else None

    # Per-VAT-rate breakdown, from the per-line rate snapshot (Phase 22
    # addition to OrderLine — see its docstring for why the resulting
    # amount alone wasn't enough).
    vat_breakdown: list[dict] = []
    if order_ids:
        vat_rows = (
            db.query(
                OrderLine.tax_rate_basis_points,
                func.coalesce(func.sum(OrderLine.line_total_minor - OrderLine.tax_minor), 0),
                func.coalesce(func.sum(OrderLine.tax_minor), 0),
            )
            .filter(OrderLine.order_id.in_(order_ids))
            .group_by(OrderLine.tax_rate_basis_points)
            .all()
        )
        vat_breakdown = [
            {
                "tax_rate_basis_points": rate if rate is not None else 0,
                "taxable_minor": int(taxable),
                "tax_minor": int(tax),
            }
            for rate, taxable, tax in vat_rows
        ]

    # Per-payment-method breakdown.
    payment_breakdown: list[dict] = []
    if order_ids:
        payment_rows = (
            db.query(OrderPayment.method, func.coalesce(func.sum(OrderPayment.amount_minor), 0), func.count())
            .filter(OrderPayment.order_id.in_(order_ids))
            .group_by(OrderPayment.method)
            .all()
        )
        payment_breakdown = [
            {"method": method.value, "amount_minor": int(amount), "count": int(count)}
            for method, amount, count in payment_rows
        ]

    # Refunds PROCESSED on this business date (not refunds against a sale
    # rung up on this date — a refund can land on a different day than
    # the original sale, and a Z-report reconciles what happened AT the
    # till today).
    total_refunds_minor = int(
        db.query(func.coalesce(func.sum(Refund.amount_minor), 0))
        .join(Order, Order.id == Refund.order_id)
        .filter(
            Order.tenant_id == tenant_id,
            Order.store_id == store_id,
            func.date(Refund.created_at) == business_date,
        )
        .scalar()
        or 0
    )

    voided_orders = (
        db.query(Order)
        .filter(
            Order.tenant_id == tenant_id,
            Order.store_id == store_id,
            Order.sales_channel == SalesChannel.POS,
            Order.status == OrderStatus.VOIDED,
            func.date(Order.voided_at) == business_date,
        )
        .all()
    )
    total_voided_minor = sum(o.total_minor for o in voided_orders)
    voided_count = len(voided_orders)

    result = DayCloseResult(
        tenant_id=tenant_id,
        store_id=store_id,
        business_date=business_date,
        is_finalized=finalize,
        total_sales_minor=total_sales_minor,
        total_tax_minor=total_tax_minor,
        total_refunds_minor=total_refunds_minor,
        total_voided_minor=total_voided_minor,
        voided_count=voided_count,
        receipt_count=receipt_count,
        first_receipt_number=first_receipt_number,
        last_receipt_number=last_receipt_number,
        vat_breakdown=vat_breakdown,
        payment_breakdown=payment_breakdown,
    )

    if finalize:
        db.add(
            DayClose(
                tenant_id=tenant_id,
                store_id=store_id,
                business_date=business_date,
                is_finalized=True,
                closed_by_user_id=closed_by_user_id,
                total_sales_minor=total_sales_minor,
                total_tax_minor=total_tax_minor,
                total_refunds_minor=total_refunds_minor,
                total_voided_minor=total_voided_minor,
                voided_count=voided_count,
                receipt_count=receipt_count,
                first_receipt_number=first_receipt_number,
                last_receipt_number=last_receipt_number,
                vat_breakdown=vat_breakdown,
                payment_breakdown=payment_breakdown,
            )
        )
        db.flush()

    return result
