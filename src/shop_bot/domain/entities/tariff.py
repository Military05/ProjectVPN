from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from shop_bot.domain.errors import DomainValidationError
from shop_bot.domain.value_objects.money import Money


@dataclass(slots=True)
class Tariff:
    id: int | None
    name: str
    price: Money
    period_days: int
    description: str | None = None
    is_enabled: bool = True

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise DomainValidationError("Tariff name cannot be blank")
        if self.price.minor <= 0:
            raise DomainValidationError("Tariff price must be positive")
        if self.period_days <= 0:
            raise DomainValidationError("Tariff duration must be positive")

    def calculate_expiration(self, starts_at: datetime) -> datetime:
        return starts_at + timedelta(days=self.period_days)

    def enable(self) -> None:
        self.is_enabled = True

    def disable(self) -> None:
        self.is_enabled = False
