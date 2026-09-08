from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from shop_bot.application.job_names import JobName
from shop_bot.domain.payments.models import NormalizedWebhookEvent, PaymentIntent


class PaymentGateway(Protocol):
    async def create_payment(self, *, order: Mapping[str, Any], return_url: str) -> PaymentIntent: ...

    async def verify_and_normalize_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> NormalizedWebhookEvent: ...


class PaymentGatewayRegistry(Protocol):
    def get(self, provider: str) -> PaymentGateway: ...


class JobQueue(Protocol):
    async def enqueue(self, job_name: JobName, *args: Any) -> None: ...


class PanelGateway(Protocol):
    async def provision(self, payload: dict[str, Any]) -> None: ...

    async def revoke(self, payload: dict[str, Any]) -> None: ...


class NodeGateway(Protocol):
    async def provision_client(
        self,
        *,
        node: Mapping[str, Any],
        credential: Mapping[str, Any],
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def revoke_client(
        self,
        *,
        node: Mapping[str, Any],
        credential: Mapping[str, Any],
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def get_health(self, *, node: Mapping[str, Any], credential: Mapping[str, Any]) -> dict[str, Any]: ...

    async def get_capabilities(self, *, node: Mapping[str, Any], credential: Mapping[str, Any]) -> dict[str, Any]: ...

    async def get_status(self, *, node: Mapping[str, Any], credential: Mapping[str, Any]) -> dict[str, Any]: ...
