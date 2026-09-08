from __future__ import annotations

from shop_bot.core.config import Settings
from shop_bot.infrastructure.payments.base import PaymentAdapter
from shop_bot.infrastructure.payments.cryptobot import CryptoBotAdapter
from shop_bot.infrastructure.payments.dummy import DummyPaymentAdapter
from shop_bot.infrastructure.payments.heleket import HeleketAdapter
from shop_bot.infrastructure.payments.ton import TonAdapter
from shop_bot.infrastructure.payments.yookassa import YooKassaAdapter


class PaymentAdapterRegistry:
    def __init__(self, settings: Settings) -> None:
        self._adapters: dict[str, PaymentAdapter] = {
            "yookassa": YooKassaAdapter(settings),
        }
        if not settings.is_production:
            self._adapters.update(
                {
                    "dummy": DummyPaymentAdapter(settings),
                    "cryptobot": CryptoBotAdapter(settings),
                    "heleket": HeleketAdapter(settings),
                    "ton": TonAdapter(settings),
                }
            )

    def get(self, provider: str) -> PaymentAdapter:
        try:
            return self._adapters[provider]
        except KeyError as exc:
            raise ValueError(f"Unsupported payment provider: {provider}") from exc
