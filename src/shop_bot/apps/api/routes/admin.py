from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from shop_bot.apps.api.deps import get_container
from shop_bot.application.commands._helpers import enqueue_job
from shop_bot.application.commands.sync_expired_subscriptions import sync_expired_subscriptions
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.security import require_admin_token
from shop_bot.core.time import utcnow
from shop_bot.schemas.admin import (
    CreateServerEndpointRequest,
    CreateServerRequest,
    CreateTariffRequest,
    UpdateTariffRequest,
)
from shop_bot.schemas.bot import TariffResponse

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_token)])


@router.get("/tariffs", response_model=list[TariffResponse])
async def list_tariffs(container: ServiceContainer = Depends(get_container)) -> list[TariffResponse]:
    async with container.uow() as uow:
        rows = await uow.admin.list_tariffs(include_disabled=True)
    return [TariffResponse.model_validate(row) for row in rows]


@router.post("/tariffs", response_model=TariffResponse, status_code=status.HTTP_201_CREATED)
async def create_tariff(
    payload: CreateTariffRequest,
    container: ServiceContainer = Depends(get_container),
) -> TariffResponse:
    async with container.uow() as uow:
        created = await uow.admin.create_tariff(
            tariff_name=payload.tariff_name,
            price_minor=payload.price_minor,
            currency=payload.currency,
            period_days=payload.period_days,
            description=payload.description,
            is_enabled=payload.is_enabled,
        )
    return TariffResponse.model_validate(created)


@router.patch("/tariffs/{tariff_id}", response_model=TariffResponse)
async def update_tariff(
    tariff_id: int,
    payload: UpdateTariffRequest,
    container: ServiceContainer = Depends(get_container),
) -> TariffResponse:
    async with container.uow() as uow:
        updated = await uow.admin.update_tariff(
            tariff_id,
            tariff_name=payload.tariff_name,
            price_minor=payload.price_minor,
            currency=payload.currency,
            period_days=payload.period_days,
            description=payload.description,
            is_enabled=payload.is_enabled,
        )

    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tariff not found")

    return TariffResponse.model_validate(updated)


@router.delete("/tariffs/{tariff_id}", response_model=TariffResponse)
async def disable_tariff(
    tariff_id: int,
    container: ServiceContainer = Depends(get_container),
) -> TariffResponse:
    async with container.uow() as uow:
        updated = await uow.admin.set_tariff_enabled(
            tariff_id,
            is_enabled=False,
        )

    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tariff not found")

    return TariffResponse.model_validate(updated)


@router.post("/tariffs/{tariff_id}/enable", response_model=TariffResponse)
async def enable_tariff(
    tariff_id: int,
    container: ServiceContainer = Depends(get_container),
) -> TariffResponse:
    async with container.uow() as uow:
        updated = await uow.admin.set_tariff_enabled(
            tariff_id,
            is_enabled=True,
        )

    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tariff not found")

    return TariffResponse.model_validate(updated)


@router.get("/servers")
async def list_servers(container: ServiceContainer = Depends(get_container)) -> list[dict]:
    async with container.uow() as uow:
        rows = await uow.servers.list_servers()
    return [dict(row) for row in rows]


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def create_server(
    payload: CreateServerRequest,
    container: ServiceContainer = Depends(get_container),
) -> dict:
    async with container.uow() as uow:
        row = await uow.servers.create_server(
            server_name=payload.server_name,
            host=payload.host,
            is_enabled=payload.is_enabled,
        )
    return dict(row)


@router.get("/server-endpoints")
async def list_server_endpoints(container: ServiceContainer = Depends(get_container)) -> list[dict]:
    async with container.uow() as uow:
        rows = await uow.servers.list_server_endpoints()
    return [dict(row) for row in rows]


@router.post("/server-endpoints", status_code=status.HTTP_201_CREATED)
async def create_server_endpoint(
    payload: CreateServerEndpointRequest,
    container: ServiceContainer = Depends(get_container),
) -> dict:
    async with container.uow() as uow:
        row = await uow.servers.create_server_endpoint(**payload.model_dump())
    return dict(row)


@router.get("/subscriptions")
async def list_subscriptions(container: ServiceContainer = Depends(get_container)) -> list[dict]:
    async with container.uow() as uow:
        rows = await uow.subscriptions.list_subscriptions()
    return [dict(row) for row in rows]


@router.get("/vpn-configurations")
async def list_vpn_configurations(container: ServiceContainer = Depends(get_container)) -> list[dict]:
    async with container.uow() as uow:
        rows = await uow.vpn.list_configurations()
    return [dict(row) for row in rows]


@router.get("/payment-orders")
async def list_payment_orders(container: ServiceContainer = Depends(get_container)) -> list[dict]:
    async with container.uow() as uow:
        rows = await uow.payments.list_orders()
    return [dict(row) for row in rows]


@router.post("/payment-orders/{payment_order_id}/mark-paid")
async def mark_payment_order_paid(
    payment_order_id: int,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, int | str]:
    now = utcnow()
    async with container.uow() as uow:
        order = await uow.payments.get_order(payment_order_id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
        attempt = await uow.payments.get_latest_attempt(payment_order_id)
        event = await uow.payments.create_payment_event(
            payment_order_id=payment_order_id,
            payment_attempt_id=int(attempt["payment_attempt_id"]) if attempt is not None else None,
            provider=str(order["provider"]),
            event_type="admin.manual.paid",
            event_key=f"admin:manual:paid:{payment_order_id}",
            status="received",
            payload={"status": "paid", "source": "admin", "payment_order_id": payment_order_id},
            occurred_at=now,
        )
    await enqueue_job(container, "process_payment_event", int(event["payment_event_id"]))
    return {"payment_event_id": int(event["payment_event_id"]), "status": "queued"}


@router.post("/subscriptions/{subscription_id}/provision")
async def queue_provisioning(
    subscription_id: int,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, int | str]:
    await enqueue_job(container, "provision_subscription", subscription_id)
    return {"subscription_id": subscription_id, "status": "queued"}


@router.post("/vpn-configurations/{vpn_configuration_id}/revoke")
async def queue_revoke(
    vpn_configuration_id: int,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, int | str]:
    await enqueue_job(container, "revoke_vpn_configuration", vpn_configuration_id)
    return {"vpn_configuration_id": vpn_configuration_id, "status": "queued"}


@router.post("/reconcile")
async def reconcile_now(container: ServiceContainer = Depends(get_container)) -> dict[str, int]:
    return dict(await sync_expired_subscriptions(container))