"""
Phase 22 (LedgerBrug dev-team question #5 — daily X/Z report).
"""
import datetime as dt

import pytest

from app.domain.authz import User
from app.domain.cash import CashierSession, DayClose
from app.domain.catalog import Product, Tax, Category
from app.domain.orders import PaymentMethod
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store
from app.services.checkout import CartLineInput, PaymentInput, create_pos_sale
from app.services.reports import DayCloseAlreadyFinalizedError, build_day_close
from app.services.voids import void_order
from app.core.security import Principal


@pytest.fixture
def store_setup(db):
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Report Test Cashier", email="report-cashier@test-fixture.local", password_hash="x")
    db.add(user)
    tax21 = Tax(tenant_id=tenant.id, name="Standard", rate_basis_points=2100)
    tax9 = Tax(tenant_id=tenant.id, name="Reduced", rate_basis_points=900)
    db.add(tax21)
    db.add(tax9)
    db.flush()
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    mug = Product(tenant_id=tenant.id, category_id=category.id, tax_id=tax21.id, sku="MUG-1", name="Mug", price_minor=1000, currency="EUR", unit="piece")
    coffee = Product(tenant_id=tenant.id, category_id=category.id, tax_id=tax9.id, sku="COFFEE-1", name="Coffee beans", price_minor=2000, currency="EUR", unit="piece")
    db.add(mug)
    db.add(coffee)
    db.flush()
    db.add(CashierSession(tenant_id=tenant.id, store_id=store.id, register_id=register.id, cashier_user_id=user.id, opening_cash_minor=0))
    db.commit()
    return {
        "tenant_id": tenant.id, "store_id": store.id, "register_id": register.id,
        "user_id": user.id, "mug": mug, "coffee": coffee,
        "principal": Principal(user_id=user.id, tenant_id=tenant.id, role="Store Manager", store_id=store.id),
    }


def _ring_up(db, s, lines, payments):
    order = create_pos_sale(
        db, tenant_id=s["tenant_id"], store_id=s["store_id"], register_id=s["register_id"],
        cashier_user_id=s["user_id"], lines=lines, payments=payments,
    )
    db.commit()
    return order


def test_x_report_totals_per_vat_rate_and_payment_method(db, store_setup):
    s = store_setup
    order = _ring_up(
        db, s,
        lines=[CartLineInput(s["mug"].id, 1), CartLineInput(s["coffee"].id, 1)],
        payments=[PaymentInput(PaymentMethod.CASH, 1210), PaymentInput(PaymentMethod.CARD, 2180)],
    )
    # mug: 1000 + 21% = 1210. coffee: 2000 + 9% = 2180. total = 3390.
    assert order.total_minor == 3390

    today = order.created_at.date()
    result = build_day_close(db, tenant_id=s["tenant_id"], store_id=s["store_id"], business_date=today, finalize=False)

    assert result.is_finalized is False
    assert result.receipt_count == 1
    assert result.total_sales_minor == 3390
    rates = {row["tax_rate_basis_points"]: row for row in result.vat_breakdown}
    assert rates[2100]["taxable_minor"] == 1000
    assert rates[2100]["tax_minor"] == 210
    assert rates[900]["taxable_minor"] == 2000
    assert rates[900]["tax_minor"] == 180
    methods = {row["method"]: row for row in result.payment_breakdown}
    assert methods["CASH"]["amount_minor"] == 1210
    assert methods["CARD"]["amount_minor"] == 2180

    # X-report never persists.
    assert db.query(DayClose).count() == 0


def test_z_report_persists_exactly_one_row_and_cannot_be_run_twice(db, store_setup):
    s = store_setup
    _ring_up(db, s, lines=[CartLineInput(s["mug"].id, 1)], payments=[PaymentInput(PaymentMethod.CASH, 1210)])
    today = dt.datetime.utcnow().date()

    result = build_day_close(db, tenant_id=s["tenant_id"], store_id=s["store_id"], business_date=today, finalize=True, closed_by_user_id=s["user_id"])
    db.commit()
    assert result.is_finalized is True
    assert db.query(DayClose).filter(DayClose.is_finalized.is_(True)).count() == 1

    with pytest.raises(DayCloseAlreadyFinalizedError):
        build_day_close(db, tenant_id=s["tenant_id"], store_id=s["store_id"], business_date=today, finalize=True, closed_by_user_id=s["user_id"])


def test_z_report_includes_first_and_last_receipt_number(db, store_setup):
    s = store_setup
    order1 = _ring_up(db, s, lines=[CartLineInput(s["mug"].id, 1)], payments=[PaymentInput(PaymentMethod.CASH, 1210)])
    order2 = _ring_up(db, s, lines=[CartLineInput(s["mug"].id, 1)], payments=[PaymentInput(PaymentMethod.CASH, 1210)])
    today = order1.created_at.date()

    result = build_day_close(db, tenant_id=s["tenant_id"], store_id=s["store_id"], business_date=today, finalize=False)
    assert result.receipt_count == 2
    assert result.first_receipt_number == order1.receipt_number
    assert result.last_receipt_number == order2.receipt_number


def test_z_report_shows_voided_sales_separately_from_completed_sales(db, store_setup):
    s = store_setup
    order = _ring_up(db, s, lines=[CartLineInput(s["mug"].id, 1)], payments=[PaymentInput(PaymentMethod.CASH, 1210)])
    void_order(db, tenant_id=s["tenant_id"], order_id=order.id, principal=s["principal"], reason="test void")
    db.commit()
    today = dt.datetime.utcnow().date()

    result = build_day_close(db, tenant_id=s["tenant_id"], store_id=s["store_id"], business_date=today, finalize=False)
    # The voided sale must not count toward today's sales total...
    assert result.total_sales_minor == 0
    assert result.receipt_count == 0
    # ...but must be visible as a voided total so the report explains the gap.
    assert result.total_voided_minor == 1210
    assert result.voided_count == 1
