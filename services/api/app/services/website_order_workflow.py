"""
Phase 11 — Website order status transition policy (plan §21/§22).

Pure function over the transition table; the transition table itself is
DB-seeded data (WebsiteOrderStatusTransition, seeded from
app.domain.website.DEFAULT_ALLOWED_TRANSITIONS) so admins can adjust it
without a redeploy — this function just enforces whatever table it's
given, so tests can also exercise custom transition sets.
"""
from __future__ import annotations

from app.domain.website import WebsiteOrderStatus, DEFAULT_ALLOWED_TRANSITIONS


class InvalidTransitionError(Exception):
    pass


def assert_valid_transition(
    current: WebsiteOrderStatus,
    target: WebsiteOrderStatus,
    allowed: dict[WebsiteOrderStatus, set[WebsiteOrderStatus]] | None = None,
) -> None:
    allowed = allowed if allowed is not None else DEFAULT_ALLOWED_TRANSITIONS
    if target not in allowed.get(current, set()):
        raise InvalidTransitionError(f"{current.value} -> {target.value} is not an allowed transition")


# Sensitive transitions that require manager approval before being applied
# (plan §21: "Manager approval may be required for sensitive transitions").
# Data-driven, not hardcoded per-role logic scattered through routes.
TRANSITIONS_REQUIRING_APPROVAL: set[tuple[WebsiteOrderStatus, WebsiteOrderStatus]] = {
    (WebsiteOrderStatus.CONFIRMED, WebsiteOrderStatus.CANCELLED),
    (WebsiteOrderStatus.PROCESSING, WebsiteOrderStatus.CANCELLED),
    (WebsiteOrderStatus.DELIVERED, WebsiteOrderStatus.REFUNDED),
    (WebsiteOrderStatus.COMPLETED, WebsiteOrderStatus.REFUNDED),
}


def requires_approval(current: WebsiteOrderStatus, target: WebsiteOrderStatus) -> bool:
    return (current, target) in TRANSITIONS_REQUIRING_APPROVAL
