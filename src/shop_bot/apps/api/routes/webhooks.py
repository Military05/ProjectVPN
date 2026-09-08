from __future__ import annotations

from ipaddress import ip_address, ip_network

from fastapi import APIRouter, Depends, HTTPException, Request, status

from shop_bot.apps.api.deps import get_container
from shop_bot.application.commands.ingest_webhook_event import ingest_webhook_event
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.exceptions import WebhookAuthenticationError, WebhookPayloadError
from shop_bot.schemas.webhooks import WebhookAcceptedResponse

router = APIRouter(tags=["webhooks"])


def _peer_ip(request: Request) -> str:
    if request.client is None or not request.client.host:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook source")
    return request.client.host


def _in_cidrs(value: str, cidrs: list[str]) -> bool:
    try:
        address = ip_address(value)
        return any(address in ip_network(cidr, strict=False) for cidr in cidrs)
    except ValueError:
        return False


def _effective_client_ip(request: Request, trusted_proxy_cidrs: list[str]) -> str:
    peer = _peer_ip(request)
    if not _in_cidrs(peer, trusted_proxy_cidrs):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not chain:
        return peer
    # Walk from the directly-connected side toward the original client. Only
    # addresses behind an explicitly trusted proxy may influence the result.
    current = peer
    for candidate in reversed(chain):
        if not _in_cidrs(current, trusted_proxy_cidrs):
            break
        try:
            ip_address(candidate)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook source")
        current = candidate
    return current


async def _read_bounded_body(request: Request, maximum: int) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            if int(raw_length) > maximum:
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="webhook body too large")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid content length") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="webhook body too large")
    return bytes(body)


async def _handle_payment_webhook(
    *, provider: str,
    request: Request,
    container: ServiceContainer,
) -> WebhookAcceptedResponse:
    settings = request.app.state.settings
    if provider.strip().lower() == "yookassa":
        source_ip = _effective_client_ip(request, settings.trusted_proxy_cidrs)
        if not _in_cidrs(source_ip, settings.yookassa_webhook_allowed_networks):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook source")
    raw_body = await _read_bounded_body(request, settings.webhook_max_body_bytes)
    try:
        result = await ingest_webhook_event(
            container,
            provider=provider,
            raw_body=raw_body,
            headers=request.headers,
        )
    except WebhookAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook") from exc
    except WebhookPayloadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid webhook payload") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unsupported payment provider") from exc
    return WebhookAcceptedResponse.model_validate(result)


@router.post("/webhooks/{provider}", response_model=WebhookAcceptedResponse)
async def payment_webhook(
    provider: str,
    request: Request,
    container: ServiceContainer = Depends(get_container),
) -> WebhookAcceptedResponse:
    return await _handle_payment_webhook(provider=provider, request=request, container=container)


@router.post("/yookassa/webhook", response_model=WebhookAcceptedResponse)
async def yookassa_webhook(
    request: Request,
    container: ServiceContainer = Depends(get_container),
) -> WebhookAcceptedResponse:
    return await _handle_payment_webhook(provider="yookassa", request=request, container=container)
