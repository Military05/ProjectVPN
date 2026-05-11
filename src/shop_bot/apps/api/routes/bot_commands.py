from __future__ import annotations

from fastapi import APIRouter, Depends

from shop_bot.apps.api.deps import get_container
from shop_bot.application.commands.create_payment_order import create_payment_order
from shop_bot.application.commands.register_bot_user import register_bot_user
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.security import require_internal_api_key
from shop_bot.schemas.bot import (
    BotCreateOrderRequest,
    BotCreateOrderResponse,
    BotRegisterRequest,
    BotUserResponse,
)

router = APIRouter(prefix="/bot", tags=["bot-command"], dependencies=[Depends(require_internal_api_key)])


@router.post("/register", response_model=BotUserResponse)
async def register_bot_user_endpoint(
    payload: BotRegisterRequest,
    container: ServiceContainer = Depends(get_container),
) -> BotUserResponse:
    row = await register_bot_user(
        container,
        telegram_id=payload.telegram_id,
        username=payload.username,
        name_or_nick=payload.name_or_nick,
    )
    return BotUserResponse.model_validate(row)


@router.post("/orders", response_model=BotCreateOrderResponse)
async def create_order_endpoint(
    payload: BotCreateOrderRequest,
    container: ServiceContainer = Depends(get_container),
) -> BotCreateOrderResponse:
    order = await create_payment_order(
        container,
        telegram_id=payload.telegram_id,
        tariff_id=payload.tariff_id,
        provider=payload.provider,
        idempotency_key=payload.idempotency_key,
        username=payload.username,
        name_or_nick=payload.name_or_nick,
    )
    return BotCreateOrderResponse.model_validate(order)
