"""
Phase 22 (LedgerBrug dev-team question #2 — split payments) and question
#3 (permanent unique receipt number). Both live in checkout.py's output,
so it's natural to test them together against the same sale.
"""
import pytest

from app.domain.authz import User
from app.domain.cash import CashierSession
from app.domain.catalog import Product, Tax, Category
from app.domain.orders import OrderPayment, PaymentMethod
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store
from app.services.checkout import CartLineInput, InvalidCartError, PaymentInput, create_pos_sale


@pytest.fixture
def store_setup(db):
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Split Payment Cashier", email="split-cashier@test-fixture.local", password_hash="x")
    db.add(user)
    tax = Tax(tenant_id=tenant.id, name="Standard", rate_basis_points=2100)  # 21%
    db.add(tax)
    db.flush()
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    product = Product(
        tenant_id=tenant.id, category_id=category.id, tax_id=tax.id, sku="SPLIT-1",
        name="Split Payment Test Item", price_minor=2500, currency="EUR", unit="piece",
    )
    db.add(product)
    db.flush()
    db.add(CashierSession(tenant_id=tenant.id, store_id=store.id, register_id=register.id, cashier_user_id=user.id, opening_cash_minor=0))
    db.commit()
    return {
        "tenant_id": tenant.id, "product": product, "user_id": user.id,
        "register_id": register.id, "store_id": register.store_id,
    }


def test_split_payment_across_cash_and_card_sums_correctly(db, store_setup):
    # 25.00 total: 10.00 cash + 15.00 card, mirroring LedgerBrug's own example.
    order = create_pos_sale(
        db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], lines=[CartLineInput(store_setup["product"].id, 1)],
        payments=[
            PaymentInput(PaymentMethod.CASH, 1000),
            PaymentInput(PaymentMethod.CARD, 2025, provider_reference="CCV-TXN-001"),
        ],
    )
    db.flush()
    assert order.total_minor == 3025  # 2500 + 21% tax
    payments = db.query(OrderPayment).filter(OrderPayment.order_id == order.id).all()
    assert len(payments) == 2
    assert {p.method for p in payments} == {PaymentMethod.CASH, PaymentMethod.CARD}
    card_payment = next(p for p in payments if p.method == PaymentMethod.CARD)
    assert card_payment.provider_reference == "CCV-TXN-001"
    assert card_payment.amount_minor == 2025
    assert sum(p.amount_minor for p in payments) == order.total_minor


def test_split_payment_tenders_must_sum_to_the_total(db, store_setup):
    with pytest.raises(InvalidCartError, match="sum to"):
        create_pos_sale(
            db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
            cashier_user_id=store_setup["user_id"], lines=[CartLineInput(store_setup["product"].id, 1)],
            payments=[PaymentInput(PaymentMethod.CASH, 1000)],  # short-tendered against a 3025 total
        )


def test_single_payment_method_path_still_works_unchanged(db, store_setup):
    """Backward compatibility: existing callers passing payment_method
    (not payments) must keep working exactly as before Phase 22."""
    order = create_pos_sale(
        db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], lines=[CartLineInput(store_setup["product"].id, 1)],
        payment_method=PaymentMethod.CASH,
    )
    db.flush()
    payments = db.query(OrderPayment).filter(OrderPayment.order_id == order.id).all()
    assert len(payments) == 1
    assert payments[0].amount_minor == order.total_minor


def test_passing_both_or_neither_payment_shape_is_rejected(db, store_setup):
    with pytest.raises(InvalidCartError):
        create_pos_sale(
            db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
            cashier_user_id=store_setup["user_id"], lines=[CartLineInput(store_setup["product"].id, 1)],
        )
    with pytest.raises(InvalidCartError):
        create_pos_sale(
            db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
            cashier_user_id=store_setup["user_id"], lines=[CartLineInput(store_setup["product"].id, 1)],
            payment_method=PaymentMethod.CASH,
            payments=[PaymentInput(PaymentMethod.CASH, 3025)],
        )


def test_receipt_number_is_permanent_unique_and_never_reused(db, store_setup):
    order1 = create_pos_sale(
        db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], lines=[CartLineInput(store_setup["product"].id, 1)],
        payment_method=PaymentMethod.CASH,
    )
    db.flush()
    order2 = create_pos_sale(
        db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], lines=[CartLineInput(store_setup["product"].id, 1)],
        payment_method=PaymentMethod.CASH,
    )
    db.flush()
    assert order1.receipt_number is not None
    assert order2.receipt_number is not None
    assert order1.receipt_number != order2.receipt_number
    # Human/barcode-readable shape: store-register-order, zero-padded.
    assert order1.receipt_number == f"{store_setup['store_id']:04d}-{store_setup['register_id']:03d}-{order1.id:010d}"
