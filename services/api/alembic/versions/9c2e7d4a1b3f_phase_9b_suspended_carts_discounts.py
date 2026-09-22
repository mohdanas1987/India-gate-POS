"""phase 9b - suspended carts, customer attach, discount approval

Adds: Order.customer_id (nullable FK to the existing customers table),
and a new held_carts table for the suspended-cart (HOLD/RECALL) feature.
No changes needed to order_lines.discount_minor or orders.discount_minor
— both already existed (Phase 7 schema) and were simply always written as
0 until this phase actually computes a real value.

Revision ID: 9c2e7d4a1b3f
Revises: 8a1c3f2e9b4d
Create Date: 2026-09-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '9c2e7d4a1b3f'
down_revision = '8a1c3f2e9b4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_orders_customer_id', 'orders', 'customers', ['customer_id'], ['id'])

    op.create_table(
        'held_carts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('store_id', sa.Integer(), sa.ForeignKey('stores.id'), nullable=False),
        sa.Column('register_id', sa.Integer(), sa.ForeignKey('registers.id'), nullable=False),
        sa.Column('cashier_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=True),
        sa.Column('label', sa.String(length=100), nullable=True),
        sa.Column('lines_json', sa.JSON(), nullable=False),
        sa.Column('held_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_held_carts_tenant_store', 'held_carts', ['tenant_id', 'store_id'])


def downgrade() -> None:
    op.drop_index('ix_held_carts_tenant_store', table_name='held_carts')
    op.drop_table('held_carts')
    op.drop_constraint('fk_orders_customer_id', 'orders', type_='foreignkey')
    op.drop_column('orders', 'customer_id')
