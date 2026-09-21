"""phase 22 - ledgerbrug integration readiness

Adds: Order.receipt_number (+ void audit fields), OrderLine.tax_rate_basis_points,
DayClose extensions (tenant_id, is_finalized, breakdowns, receipt range),
LedgerEventType.VOID, and the new ledgerbrug_outbox_events table.

Revision ID: 8a1c3f2e9b4d
Revises: 4f2b5a6b27f4
Create Date: 2026-09-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8a1c3f2e9b4d'
down_revision = '4f2b5a6b27f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- orders: receipt number + void audit fields ---
    op.add_column('orders', sa.Column('receipt_number', sa.String(length=40), nullable=True))
    op.create_unique_constraint('uq_orders_receipt_number', 'orders', ['receipt_number'])
    op.add_column('orders', sa.Column('voided_by_user_id', sa.Integer(), nullable=True))
    op.add_column('orders', sa.Column('voided_reason', sa.String(length=500), nullable=True))
    op.add_column('orders', sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('fk_orders_voided_by_user_id', 'orders', 'users', ['voided_by_user_id'], ['id'])

    # --- order_lines: per-line VAT rate snapshot ---
    op.add_column('order_lines', sa.Column('tax_rate_basis_points', sa.Integer(), nullable=True))

    # --- day_closes: tenant scoping (a real gap — this table had none)
    # plus everything the Z-report needs to actually be useful ---
    op.add_column('day_closes', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_day_closes_tenant_id', 'day_closes', 'tenants', ['tenant_id'], ['id'])
    op.add_column('day_closes', sa.Column('is_finalized', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('day_closes', sa.Column('closed_by_user_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_day_closes_closed_by_user_id', 'day_closes', 'users', ['closed_by_user_id'], ['id'])
    op.add_column('day_closes', sa.Column('total_voided_minor', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('day_closes', sa.Column('voided_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('day_closes', sa.Column('receipt_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('day_closes', sa.Column('first_receipt_number', sa.String(length=40), nullable=True))
    op.add_column('day_closes', sa.Column('last_receipt_number', sa.String(length=40), nullable=True))
    op.add_column('day_closes', sa.Column('vat_breakdown', sa.JSON(), nullable=True))
    op.add_column('day_closes', sa.Column('payment_breakdown', sa.JSON(), nullable=True))
    # A finalized Z-report must be unique per (tenant, store, business_date)
    # in the database itself, not just in application code — this is the
    # actual enforcement behind "cannot finalize the same day twice"
    # (app/services/reports.py raises DayCloseAlreadyFinalizedError as the
    # friendly path, but a race between two concurrent finalize calls
    # needs the DB constraint as the real backstop).
    op.create_unique_constraint(
        'uq_day_closes_tenant_store_date_finalized',
        'day_closes',
        ['tenant_id', 'store_id', 'business_date', 'is_finalized'],
    )

    # --- inventory_ledger: new enum value for void reversals ---
    op.execute("ALTER TYPE ledgereventtype ADD VALUE IF NOT EXISTS 'VOID'")

    # --- ledgerbrug_outbox_events: new table ---
    op.create_table(
        'ledgerbrug_outbox_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'SENT', 'FAILED', name='ledgerbrugeventstatus'), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_error', sa.String(length=2000), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_ledgerbrug_outbox_events_pending_due',
        'ledgerbrug_outbox_events',
        ['status', 'next_attempt_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_ledgerbrug_outbox_events_pending_due', table_name='ledgerbrug_outbox_events')
    op.drop_table('ledgerbrug_outbox_events')
    op.execute("DROP TYPE IF EXISTS ledgerbrugeventstatus")

    op.drop_constraint('uq_day_closes_tenant_store_date_finalized', 'day_closes', type_='unique')
    op.drop_column('day_closes', 'payment_breakdown')
    op.drop_column('day_closes', 'vat_breakdown')
    op.drop_column('day_closes', 'last_receipt_number')
    op.drop_column('day_closes', 'first_receipt_number')
    op.drop_column('day_closes', 'receipt_count')
    op.drop_column('day_closes', 'voided_count')
    op.drop_column('day_closes', 'total_voided_minor')
    op.drop_constraint('fk_day_closes_closed_by_user_id', 'day_closes', type_='foreignkey')
    op.drop_column('day_closes', 'closed_by_user_id')
    op.drop_column('day_closes', 'is_finalized')
    op.drop_constraint('fk_day_closes_tenant_id', 'day_closes', type_='foreignkey')
    op.drop_column('day_closes', 'tenant_id')

    op.drop_column('order_lines', 'tax_rate_basis_points')

    op.drop_constraint('fk_orders_voided_by_user_id', 'orders', type_='foreignkey')
    op.drop_column('orders', 'voided_at')
    op.drop_column('orders', 'voided_reason')
    op.drop_column('orders', 'voided_by_user_id')
    op.drop_constraint('uq_orders_receipt_number', 'orders', type_='unique')
    op.drop_column('orders', 'receipt_number')

    # Note: Postgres cannot remove a value from an existing enum type
    # without recreating it; VOID is left in ledgereventtype on downgrade,
    # same limitation Postgres itself imposes (not something this
    # migration can safely work around without a full type rebuild).
