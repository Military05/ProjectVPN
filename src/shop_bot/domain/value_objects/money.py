from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from shop_bot.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class Money:
    """Immutable monetary value stored in minor currency units."""

    minor: int
    currency: str

    def __post_init__(self) -> None:
        normalized_currency = self.currency.strip().upper()
        if self.minor < 0:
            raise DomainValidationError("Money amount cannot be negative")
        if not normalized_currency:
            raise DomainValidationError("Currency cannot be blank")
        object.__setattr__(self, "currency", normalized_currency)

    @classmethod
    def from_major(cls, amount: Decimal | str | int | float, currency: str) -> Money:
        decimal_amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return cls(minor=int(decimal_amount * 100), currency=currency)

    @property
    def major(self) -> Decimal:
        return (Decimal(self.minor) / Decimal(100)).quantize(Decimal("0.01"))

    def __add__(self, other: Money) -> Money:
        self._ensure_same_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._ensure_same_currency(other)
        if other.minor > self.minor:
            raise DomainValidationError("Money result cannot be negative")
        return Money(self.minor - other.minor, self.currency)

    def _ensure_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise DomainValidationError("Money values must use the same currency")
