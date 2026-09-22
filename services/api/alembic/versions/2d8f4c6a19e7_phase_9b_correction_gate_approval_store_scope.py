"""Phase 9B correction gate — store-scope discount/refund approvals

CTO review of 96f6aa9, security issue #2: Approval had no store_id, so a
store-scoped manager in ANY store of the tenant could list and resolve
another store's pending approval (both the discount and refund flows
share the same Approval table/routes). This adds the column, backfills it
from existing rows' own context where possible, and app/api/v1/approvals.py
now enforces resolver.store_id == approval.store_id unless the resolver
holds the new org-wide "approvals.manage.all_stores" permission.

Revision ID: 2d8f4c6a19e7
Revises: 9c2e7d4a1b3f
Create Date: 2026-09-22
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "2d8f4c6a19e7"
down_revision = "9c2e7d4a1b3f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("store_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_approvals_store_id", "approvals", "stores", ["store_id"], ["id"])
    op.create_index("ix_approvals_tenant_store", "approvals", ["tenant_id", "store_id"])

    # Backfill: a discount approval's context already carries store_id
    # directly; a refund approval's context carries order_id, from which
    # the order's own store_id can be looked up. Existing REQUESTED rows
    # predating this migration that can't be backfilled (e.g. a malformed
    # context) are left NULL, which app/api/v1/approvals.py treats as
    # "visible tenant-wide" rather than silently hiding them — a NULL
    # store_id is deliberately the more permissive, not more restrictive,
    # default for pre-existing data, since there's no way to know which
    # store originated it.
    conn = op.get_bind()
    # The `context` column is plain `json` (not `jsonb`), which has no `?`
    # containment operator — cast to jsonb for the existence check.
    conn.execute(
        sa.text(
            """
            UPDATE approvals
            SET store_id = CAST(context->>'store_id' AS INTEGER)
            WHERE action_code = 'orders.discount' AND context::jsonb ? 'store_id'
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE approvals a
            SET store_id = o.store_id
            FROM orders o
            WHERE a.action_code = 'orders.refund'
              AND a.context::jsonb ? 'order_id'
              AND o.id = CAST(a.context->>'order_id' AS INTEGER)
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_approvals_tenant_store", table_name="approvals")
    op.drop_constraint("fk_approvals_store_id", "approvals", type_="foreignkey")
    op.drop_column("approvals", "store_id")
