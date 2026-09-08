from __future__ import annotations

from dataclasses import dataclass

from shop_bot.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class EntityId:
    value: int

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise DomainValidationError("Entity identifier must be positive")

    def __int__(self) -> int:
        return self.value


class UserId(EntityId):
    pass


class TariffId(EntityId):
    pass


class SubscriptionId(EntityId):
    pass


class PaymentOrderId(EntityId):
    pass


class PaymentEventId(EntityId):
    pass


class VpnConfigurationId(EntityId):
    pass


class NodeId(EntityId):
    pass


class NodeTaskId(EntityId):
    pass
