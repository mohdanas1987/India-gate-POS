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


def test_compute_line_total_for_a_weighted_product_prices_by_grams_not_by_whole_kilos(db, store_setup):
    """Phase 9A finding: compute_line_total() used to treat `quantity`
    as a plain multiplier for every product, ignoring is_weighted and the
    documented convention (OrderLine.quantity's own docstring) that a
    weighted product's quantity is GRAMS while price_minor is per
    KILOGRAM. 250g of a 4.00 EUR/kg product must cost 1.00 EUR (100 minor
    units), not 250 x 400 = 100000 minor units (a 4000x overcharge)."""
    tax = store_setup["tax"]
    weighted_product = Product(
        tenant_id=store_setup["tenant_id"], category_id=store_setup["product"].category_id, tax_id=tax.id,
        sku="TOMATOES-KG", name="Tomatoes", price_minor=400, currency="EUR", unit="kg", is_weighted=True,
    )
    db.add(weighted_product)
    db.flush()

    subtotal, tax_amt, total = compute_line_total(weighted_product, tax, quantity=250)  # 250 grams
    assert subtotal == 100  # 0.25 kg x 4.00 EUR/kg = 1.00 EUR = 100 minor units
    assert tax_amt == round(100 * 0.21)
    assert total == subtotal + tax_amt


def test_compute_line_total_weighted_rounds_to_the_nearest_minor_unit(db, store_setup):
    """333g of a 3.33 EUR/kg product: 333 * 333 / 1000 = 110.889 -> rounds
    to 111 minor units, not truncated to 110 — every weighted sale should
    round the same way Money.percentage() already rounds tax, not lose a
    cent through silent truncation."""
    tax = store_setup["tax"]
    weighted_product = Product(
        tenant_id=store_setup["tenant_id"], category_id=store_setup["product"].category_id, tax_id=tax.id,
        sku="OLIVES-KG", name="Olives", price_minor=333, currency="EUR", unit="kg", is_weighted=True,
    )
    db.add(weighted_product)
    db.flush()

    subtotal, _, _ = compute_line_total(weighted_product, tax, quantity=333)
    assert subtotal == 111


def test_weighted_product_checkout_end_to_end_charges_correctly(db, store_setup):
    """Same bug, proven through the full create_pos_sale() path a real
    checkout actually uses — not just the pure function in isolation."""
    tax = store_setup["tax"]
    weighted_product = Product(
        tenant_id=store_setup["tenant_id"], category_id=store_setup["product"].category_id, tax_id=tax.id,
        sku="CHEESE-KG", name="Gouda Cheese", price_minor=1200, currency="EUR", unit="kg", is_weighted=True,
    )
    db.add(weighted_product)
    db.flush()
    db.add(CashierSession(
        tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], opening_cash_minor=10000, is_open=True,
    ))
    db.commit()

    order = create_pos_sale(
        db, tenant_id=store_setup["tenant_id"], store_id=store_setup["store_id"], register_id=store_setup["register_id"],
        cashier_user_id=store_setup["user_id"], lines=[CartLineInput(weighted_product.id, 500)],  # 500g
        payment_method=PaymentMethod.CASH,
    )
    # 0.5 kg x 12.00 EUR/kg = 6.00 EUR = 600 minor units
    assert order.subtotal_minor == 600
    assert order.tax_minor == round(600 * 0.21)

    ledger_row = db.query(InventoryLedger).filter(InventoryLedger.reference_id == str(order.id)).first()
    # Inventory is decremented in the product's own native unit (grams
    # for a weighted product), consistent with quantity_delta's existing
    # signed-integer convention — not silently converted to kilos.
    assert ledger_row.quantity_delta == -500


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
