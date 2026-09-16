from app.core.money import Money


def test_from_major_parses_dot_decimal():
    assert Money.from_major("12.99").minor_units == 1299


def test_from_major_parses_comma_decimal_legacy_format():
    # Legacy POS stored/entered prices like "12,99" — must parse identically.
    assert Money.from_major("12,99").minor_units == 1299


def test_from_major_parses_with_spaces_legacy_format():
    assert Money.from_major(" 12, 99 ").minor_units == 1299


def test_addition_never_touches_floats():
    a = Money.from_major("10.10")
    b = Money.from_major("0.05")
    assert (a + b).minor_units == 1015


def test_currency_mismatch_raises():
    import pytest

    a = Money(100, "EUR")
    b = Money(100, "INR")
    with pytest.raises(ValueError):
        a + b


def test_to_major_str_roundtrip():
    assert Money.from_major("1234.56").to_major_str() == "1234.56"
