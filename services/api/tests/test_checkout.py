import pytest

from app.domain.authz import User
from app.domain.cash import CashierSession
from app.domain.catalog import Product, Tax, Category
from app.domain.inventory import InventoryLedger
from app.domain.orders import PaymentMethod
from app.domain.seed import seed_all
from app.domain.tenancy import Register, Store
from app.services.checkout import CartLineInput, NoOpenSessionError, InvalidCartError, create_pos_sale, compute_line_total


@pytest.fixture
def store_setup(db):
    tenant = seed_all(db)
    user = User(tenant_id=tenant.id, name="Test Cashier", email="cashier@test-fixture.local", password_hash="x")
    db.add(user)
    tax = Tax(tenant_id=tenant.id, name="Standard", rate_basis_points=2100)  # 21%
    db.add(tax)
    db.flush()
    category = db.query(Category).filter(Category.slug == "grocery", Category.tenant_id == tenant.id).first()
    store = db.query(Store).filter(Store.tenant_id == tenant.id).first()
    register = db.query(Register).filter(Register.store_id == store.id).first()
    product = Product(
        tenant_id=tenant.id, category_id=category.id, tax_id=tax.id, sku="RICE-5KG",
        name="Basmati Rice 5kg", price_minor=1299, currency="EUR", unit="piece",
    )
    db.add(product)
    db.flush()
    db.commit()
    return {
        "tenant_id": tenant.id, "tax": tax, "product": product, "user_id": user.id,
        "register_id": register.id, "store_id": register.store_id,
    }


def test_compute_line_total_applies_tax_correctly(store_setup):
    product = store_setup["product"]
    tax = store_setup["tax"]
    subtotal, tax_amt, total = compute_line_total(product, tax, quantity=2)
    assert subtotal == 2598  # 2 x 1299
    assert tax_amt == round(2598 * 0.21)
    assert total == subtotal + tax_amt


def test_checkout_fails_without_open_session(db, store_setup):
    product = store_setup["product"]
    with pytest.raises(NoOpenSessionError):
        create_pos_sale(
            db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
            cashier_user_id=store_setup["user_id"], lines=[CartLineInput(product.id, 1)], payment_method=PaymentMethod.CASH,
        )


def test_checkout_rejects_empty_cart(db, store_setup):
    db.add(CashierSession(
        tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], opening_cash_minor=10000, is_open=True,
    ))
    db.commit()
    with pytest.raises(InvalidCartError):
        create_pos_sale(
            db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
            cashier_user_id=store_setup["user_id"], lines=[], payment_method=PaymentMethod.CASH,
        )


def test_successful_checkout_writes_order_and_ledger(db, store_setup):
    product = store_setup["product"]
    db.add(CashierSession(
        tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], opening_cash_minor=10000, is_open=True,
    ))
    db.commit()

    order = create_pos_sale(
        db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], lines=[CartLineInput(product.id, 3)], payment_method=PaymentMethod.CASH,
    )

    assert order.status.value == "COMPLETED"
    assert order.subtotal_minor == 1299 * 3
    assert order.total_minor == order.subtotal_minor + order.tax_minor
    assert len(order.lines) == 1
    assert order.lines[0].quantity == 3

    ledger_rows = db.query(InventoryLedger).filter(InventoryLedger.reference_id == str(order.id)).all()
    assert len(ledger_rows) == 1
    assert ledger_rows[0].quantity_delta == -3
    assert ledger_rows[0].event_type.value == "SALE"


def test_checkout_rejects_deleted_product(db, store_setup):
    product = store_setup["product"]
    product.is_deleted = True
    db.commit()
    db.add(CashierSession(
        tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], opening_cash_minor=10000, is_open=True,
    ))
    db.commit()
    with pytest.raises(InvalidCartError):
        create_pos_sale(
            db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
            cashier_user_id=store_setup["user_id"], lines=[CartLineInput(product.id, 1)], payment_method=PaymentMethod.CASH,
        )
