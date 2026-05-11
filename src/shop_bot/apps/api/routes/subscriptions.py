from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from shop_bot.apps.api.deps import get_container
from shop_bot.application.queries.subscription_feed import (
    get_subscription_feed,
    subscription_userinfo_header,
)
from shop_bot.bootstrap.container import ServiceContainer
from shop_bot.core.exceptions import NotFoundError

router = APIRouter(tags=["subscriptions"])


@router.get("/sub/{client_uuid}", response_class=PlainTextResponse)
async def subscription_feed_endpoint(
    client_uuid: str,
    container: ServiceContainer = Depends(get_container),
) -> PlainTextResponse:
    feed = await get_subscription_feed(
        container,
        client_uuid=client_uuid,
    )

    if feed is None:
        raise NotFoundError("Subscription link was not found or is not active")

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Profile-Title": feed.profile_title,
        "Subscription-Userinfo": subscription_userinfo_header(feed),
        "Cache-Control": "no-store",
    }

    return PlainTextResponse(
        content=feed.body,
        headers=headers,
    )
