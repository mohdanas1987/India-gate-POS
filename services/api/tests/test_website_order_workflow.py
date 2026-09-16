import pytest

from app.domain.website import WebsiteOrderStatus as S
from app.services.website_order_workflow import assert_valid_transition, InvalidTransitionError, requires_approval


@pytest.mark.parametrize(
    "current,target",
    [
        (S.NEW, S.CONFIRMED),
        (S.NEW, S.CANCELLED),
        (S.CONFIRMED, S.PROCESSING),
        (S.PROCESSING, S.PACKING),
        (S.PACKING, S.READY_FOR_DISPATCH),
        (S.READY_FOR_DISPATCH, S.OUT_FOR_DELIVERY),
        (S.OUT_FOR_DELIVERY, S.DELIVERED),
        (S.DELIVERED, S.COMPLETED),
    ],
)
def test_allowed_transitions_pass(current, target):
    assert_valid_transition(current, target)  # should not raise


@pytest.mark.parametrize(
    "current,target",
    [
        (S.NEW, S.DELIVERED),  # skipping the whole workflow
        (S.NEW, S.OUT_FOR_DELIVERY),
        (S.CANCELLED, S.CONFIRMED),  # cancelled is terminal
        (S.COMPLETED, S.NEW),
        (S.PACKING, S.CANCELLED),  # not in the allowed set per plan §21's example
    ],
)
def test_disallowed_transitions_raise(current, target):
    with pytest.raises(InvalidTransitionError):
        assert_valid_transition(current, target)


def test_sensitive_transitions_require_approval():
    assert requires_approval(S.CONFIRMED, S.CANCELLED) is True
    assert requires_approval(S.DELIVERED, S.REFUNDED) is True
    assert requires_approval(S.NEW, S.CONFIRMED) is False
