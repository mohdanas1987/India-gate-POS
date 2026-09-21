"""
Phase 22 — LedgerBrug (bookkeeping/VAT) integration readiness.

This is the outbound half of the sync architecture we already proved out
inbound (app/api/v1/sync.py, apps/pos/desktop's OutboxSyncEngine): queue
the event locally, deliver it with retry/backoff, never lose one to a bad
connection. It is deliberately a queue-and-send model, not a fire-and-hope
HTTP call inline in the checkout path — checkout must never fail or slow
down because LedgerBrug's endpoint is briefly unreachable.

IMPORTANT, stated plainly: the exact payload shape below (see
app/services/ledgerbrug.py::build_event_payload) is our best-guess default,
written from what we told LedgerBrug's dev team we could support. It is
NOT validated against their actual `ledgerbrug-pos-event-contract.md` —
that file was referenced by their email but never received. Field names,
nesting, and auth-header format here should be treated as a draft to
reconcile against their real contract once it arrives, not as a finished
integration.
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import String, ForeignKey, Integer, DateTime, Enum, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LedgerBrugEventStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"  # exhausted retries — needs human attention, not auto-retried further


class LedgerBrugOutboxEvent(Base):
    __tablename__ = "ledgerbrug_outbox_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))

    # "order.completed" | "order.refunded" | "order.voided" — kept as a
    # plain string, not an enum, since the real event-type vocabulary is
    # LedgerBrug's contract to define, not ours to fix in a DB enum before
    # we've seen it.
    event_type: Mapped[str] = mapped_column(String(50))

    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[LedgerBrugEventStatus] = mapped_column(Enum(LedgerBrugEventStatus), default=LedgerBrugEventStatus.PENDING)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
