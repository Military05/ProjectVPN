from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from shop_bot.apps.bot.clients.backend_api import BackendApiClient
from shop_bot.apps.bot.handlers import setup_router
from shop_bot.bootstrap.container import build_container


async def run_bot() -> None:
    container = await build_container(include_arq_pool=False, service_name="shopbot-bot")

    bot = Bot(
        token=container.settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = Dispatcher()
    backend = BackendApiClient(container.settings)

    dispatcher.include_router(
        setup_router(
            backend=backend,
            deduplicator=container.deduplicator,
            provider=container.settings.payment_default_provider,
            dedup_ttl_seconds=container.settings.bot_dedup_ttl_seconds,
        )
    )

    try:
        await dispatcher.start_polling(bot)
    finally:
        await backend.close()
        await bot.session.close()
        await container.close()
