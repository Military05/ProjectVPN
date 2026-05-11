from __future__ import annotations

from fastapi import Request

from shop_bot.bootstrap.container import ServiceContainer


async def get_container(request: Request) -> ServiceContainer:
    return request.app.state.container
