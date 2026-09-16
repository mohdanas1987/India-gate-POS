"""
Money value object — integer minor units, never floats.

Phase 0 finding: the legacy POS stores prices as `varchar` strings and does
ad-hoc `.replace(/\\s+/g,'')` / comma-to-dot cleanup in JS before doing
floating point arithmetic. That is a real bug surface (rounding drift over
66k+ orders). This module is the replacement primitive used everywhere
money is handled: prices, taxes, discounts, payments, refunds, cash,
purchase costs, margins (plan §13).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Money:
    minor_units: int  # e.g. 1299 for €12.99
    currency: str = "EUR"

    def __post_init__(self):
        if not isinstance(self.minor_units, int):
            raise TypeError("Money.minor_units must be an int (minor units) — never a float")

    @classmethod
    def from_major(cls, amount: str | float, currency: str = "EUR") -> "Money":
        """Parse a human-entered amount ('12.99', '12,99', 12.99) into minor units."""
        s = str(amount).strip().replace(" ", "").replace(",", ".")
        major, _, minor = s.partition(".")
        minor = (minor + "00")[:2]
        sign = -1 if major.startswith("-") else 1
        major = major.lstrip("-") or "0"
        return cls(sign * (int(major) * 100 + int(minor)), currency)

    def to_major_str(self) -> str:
        sign = "-" if self.minor_units < 0 else ""
        abs_units = abs(self.minor_units)
        return f"{sign}{abs_units // 100}.{abs_units % 100:02d}"

    def __add__(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.minor_units - other.minor_units, self.currency)

    def __mul__(self, factor: int) -> "Money":
        return Money(self.minor_units * factor, self.currency)

    def percentage(self, pct: str | float) -> "Money":
        """Compute a percentage (e.g. tax) with banker's rounding-free integer math."""
        pct_minor = Money.from_major(pct).minor_units  # pct expressed as e.g. '21' -> 2100 minor "percent-units"
        # pct given as a plain number like 21 (%), not money; handle both shapes defensively.
        if isinstance(pct, (int, float)):
            numerator = self.minor_units * round(float(pct) * 100)
        else:
            numerator = self.minor_units * pct_minor
        return Money(round(numerator / 10000), self.currency)

    def _assert_same_currency(self, other: "Money"):
        if self.currency != other.currency:
            raise ValueError(f"Currency mismatch: {self.currency} vs {other.currency}")

    def __repr__(self):
        return f"Money({self.to_major_str()} {self.currency})"
