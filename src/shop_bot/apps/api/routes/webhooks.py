from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from shop_bot.apps.api.deps import get_container
from shop_bot.application.commands.ingest_webhook_event import ingest_webhook_event
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.schemas.webhooks import WebhookAcceptedResponse

router = APIRouter(tags=["webhooks"])


async def _handle_payment_webhook(
    *,
    provider: str,
    request: Request,
    container: ServiceContainer,
) -> WebhookAcceptedResponse:
    payload = await request.json()

    result = await ingest_webhook_event(
        container,
        provider=provider,
        payload=payload,
        headers=dict(request.headers),
    )

    return WebhookAcceptedResponse.model_validate(result)


@router.post("/webhooks/{provider}", response_model=WebhookAcceptedResponse)
async def payment_webhook(
    provider: str,
    request: Request,
    container: ServiceContainer = Depends(get_container),
) -> WebhookAcceptedResponse:
    return await _handle_payment_webhook(
        provider=provider,
        request=request,
        container=container,
    )


@router.post("/yookassa/webhook", response_model=WebhookAcceptedResponse)
async def yookassa_webhook(
    request: Request,
    container: ServiceContainer = Depends(get_container),
) -> WebhookAcceptedResponse:
    return await _handle_payment_webhook(
        provider="yookassa",
        request=request,
        container=container,
    )