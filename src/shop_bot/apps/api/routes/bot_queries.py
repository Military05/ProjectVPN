from __future__ import annotations

from fastapi import APIRouter, Depends

from shop_bot.apps.api.deps import get_container
from shop_bot.application.queries.bot import get_user_dashboard, list_bot_tariffs
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.security import require_internal_api_key
from shop_bot.schemas.bot import BotDashboardResponse, TariffResponse

router = APIRouter(prefix="/bot", tags=["bot-query"], dependencies=[Depends(require_internal_api_key)])


@router.get("/tariffs", response_model=list[TariffResponse])
async def list_tariffs_endpoint(container: ServiceContainer = Depends(get_container)) -> list[TariffResponse]:
    rows = await list_bot_tariffs(container)
    return [TariffResponse.model_validate(row) for row in rows]


@router.get("/dashboard", response_model=BotDashboardResponse)
async def dashboard_endpoint(
    telegram_id: int,
    container: ServiceContainer = Depends(get_container),
) -> BotDashboardResponse:
    payload = await get_user_dashboard(container, telegram_id=telegram_id)
    return BotDashboardResponse.model_validate(payload)
