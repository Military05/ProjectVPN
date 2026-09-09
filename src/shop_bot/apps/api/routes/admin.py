from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status

from shop_bot.apps.api.deps import get_container
from shop_bot.apps.api.pagination import set_pagination_headers
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.security import require_admin_token
from shop_bot.schemas.admin import CreateServerEndpointRequest, CreateServerRequest, CreateTariffRequest
from shop_bot.schemas.bot import TariffResponse

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_token)])


@router.get("/tariffs", response_model=list[TariffResponse])
async def list_tariffs(container: ServiceContainer = Depends(get_container)) -> list[TariffResponse]:
    rows = await container.applications.admin.list_tariffs()
    return [TariffResponse.model_validate(row) for row in rows]


@router.post("/tariffs", response_model=TariffResponse, status_code=status.HTTP_201_CREATED)
async def create_tariff(
    payload: CreateTariffRequest,
    container: ServiceContainer = Depends(get_container),
) -> TariffResponse:
    created = await container.applications.admin.create_tariff(**payload.model_dump())
    return TariffResponse.model_validate(created)


@router.get("/servers")
async def list_servers(container: ServiceContainer = Depends(get_container)) -> list[dict[str, Any]]:
    return await container.applications.admin.list_servers()


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def create_server(
    payload: CreateServerRequest,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, Any]:
    return await container.applications.admin.create_server(**payload.model_dump(mode="python"))


@router.get("/server-endpoints")
async def list_server_endpoints(
    container: ServiceContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    return await container.applications.admin.list_server_endpoints()


@router.post("/server-endpoints", status_code=status.HTTP_201_CREATED)
async def create_server_endpoint(
    payload: CreateServerEndpointRequest,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, Any]:
    return await container.applications.admin.create_server_endpoint(**payload.model_dump(mode="python"))


@router.get("/subscriptions")
async def list_subscriptions(
    response: Response,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    container: ServiceContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    rows = await container.applications.admin.list_subscriptions(limit=limit + 1, offset=offset)
    set_pagination_headers(response, limit=limit, offset=offset, has_more=len(rows) > limit)
    return rows[:limit]


@router.get("/vpn-configurations")
async def list_vpn_configurations(
    response: Response,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    container: ServiceContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    rows = await container.applications.admin.list_vpn_configurations(limit=limit + 1, offset=offset)
    set_pagination_headers(response, limit=limit, offset=offset, has_more=len(rows) > limit)
    return rows[:limit]


@router.get("/payment-orders")
async def list_payment_orders(
    response: Response,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    container: ServiceContainer = Depends(get_container),
) -> list[dict[str, Any]]:
    rows = await container.applications.admin.list_payment_orders(limit=limit + 1, offset=offset)
    set_pagination_headers(response, limit=limit, offset=offset, has_more=len(rows) > limit)
    return rows[:limit]


@router.post("/payment-orders/{payment_order_id}/mark-paid")
async def mark_payment_order_paid(
    payment_order_id: int,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, int | str]:
    return await container.applications.admin.mark_payment_order_paid(payment_order_id)


@router.post("/subscriptions/{subscription_id}/provision")
async def queue_provisioning(
    subscription_id: int,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, int | str]:
    return await container.applications.admin.queue_provisioning(subscription_id)


@router.post("/vpn-configurations/{vpn_configuration_id}/revoke")
async def queue_revoke(
    vpn_configuration_id: int,
    container: ServiceContainer = Depends(get_container),
) -> dict[str, int | str]:
    return await container.applications.admin.queue_revoke(vpn_configuration_id)


@router.post("/reconcile")
async def reconcile_now(container: ServiceContainer = Depends(get_container)) -> dict[str, int]:
    return await container.applications.admin.reconcile()
