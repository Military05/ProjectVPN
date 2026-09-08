from __future__ import annotations

import logging
import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Any
from urllib.parse import urlparse

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from shop_bot.apps.bot.clients.backend_api import BackendApiClient
from shop_bot.apps.bot.keyboards import (
    BACK_BUTTON,
    CONNECT_BUTTON,
    HELP_BUTTON,
    INSTRUCTIONS_BUTTON,
    KEYS_BUTTON,
    backend_error_keyboard,
    help_keyboard,
    home_active_pending_keyboard,
    home_active_ready_keyboard,
    home_inactive_keyboard,
    home_only_keyboard,
    instructions_keyboard,
    my_vpn_inactive_keyboard,
    my_vpn_pending_keyboard,
    my_vpn_ready_keyboard,
    order_error_keyboard,
    payment_dev_local_keyboard,
    payment_ready_keyboard,
    payment_unavailable_keyboard,
    route_loading_keyboard,
    tariff_refresh_keyboard,
    tariffs_keyboard,
)
from shop_bot.infrastructure.redis.dedup import RedisDeduplicator
from shop_bot.schemas.bot import BotCreateOrderResponse, BotDashboardResponse, TariffResponse


logger = logging.getLogger(__name__)

HOME_LOADING_TEXT = "<b>Soter VPN</b>\n\nЗагружаю состояние…"
TARIFF_LOADING_TEXT = "<b>Тарифы</b>\n\nЗагружаю доступные варианты…"
MY_VPN_LOADING_TEXT = "<b>Мой VPN</b>\n\nОбновляю статус…"

HELP_TEXT = """<b>Помощь</b>

Если ключ не появился — откройте «Мой VPN» и обновите статус.

Если VPN не подключается — откройте инструкцию для вашего устройства.

Если после оплаты доступ не появился — откройте «Мой VPN» позже ещё раз."""

INSTRUCTIONS_TEXT = """<b>Как подключить VPN</b>

1. Откройте «Мой VPN» и скопируйте ключ подключения.
2. Выберите устройство ниже.
3. Откройте инструкцию и импортируйте ключ в приложение."""

BACKEND_ERROR_TEXT = """<b>Сервис временно недоступен</b>

Не удалось получить данные. Повторите действие через несколько секунд."""

NETWORK_ERROR_TEXT = """<b>Не удалось связаться с сервисом</b>

Повторите действие через несколько секунд."""

USER_RECOVERABLE_ERROR_TEXT = """<b>Не удалось выполнить действие</b>

Проверьте выбранное состояние и повторите."""

UNKNOWN_ERROR_TEXT = """<b>Что-то пошло не так</b>

Повторите действие."""

UNKNOWN_INPUT_TEXT = """<b>Не понял сообщение</b>

Используйте кнопки интерфейса или откройте главное меню."""

STALE_ACTION_TEXT = """<b>Эта кнопка устарела</b>

Откройте актуальное главное меню и повторите действие."""

TARIFF_EMPTY_TEXT = """<b>Тарифы временно недоступны</b>

Сейчас нет доступных тарифов. Обновите список позже."""

TARIFF_STALE_TEXT = """<b>Тариф больше недоступен</b>

Список тарифов изменился. Обновите его и выберите доступный вариант."""

ORDER_ERROR_TEXT = """<b>Не удалось создать оплату</b>

Запрос не завершился. Повторите действие или вернитесь к тарифам."""


@dataclass(frozen=True, slots=True)
class OrderIntentContext:
    tariff_name: str
    price_display: str


class BotMessagePresenter:
    async def send_message(
        self,
        message: Message,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        disable_web_page_preview: bool = True,
    ) -> Message:
        return await message.answer(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )

    async def render_callback(
        self,
        callback: CallbackQuery,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        disable_web_page_preview: bool = True,
        preserve_source: bool = False,
    ) -> Message | None:
        source = callback.message
        if not isinstance(source, Message):
            return None
        if preserve_source or _requires_new_message(source):
            return await source.answer(
                text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        await source.edit_text(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        return source

    @staticmethod
    async def replace_message(
        message: Message,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        disable_web_page_preview: bool = True,
    ) -> None:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )


class TelegramBotController:
    def __init__(
        self,
        *,
        backend: BackendApiClient,
        deduplicator: RedisDeduplicator,
        provider: str,
        presenter: BotMessagePresenter | None = None,
    ) -> None:
        self._backend = backend
        self._deduplicator = deduplicator
        self._provider = provider
        self._presenter = presenter or BotMessagePresenter()
        self._order_intents: dict[tuple[int, int, str], OrderIntentContext] = {}

    def build_router(self) -> Router:
        router = Router()

        router.message.register(self.start, Command("start"))
        router.message.register(self.menu, Command("menu"))
        router.message.register(self.menu, F.text == BACK_BUTTON)
        router.message.register(self.plans, Command("plans"))
        router.message.register(self.plans, F.text == CONNECT_BUTTON)
        router.message.register(self.status, Command("status"))
        router.message.register(self.status, F.text == KEYS_BUTTON)
        router.message.register(self.help_message, Command("help"))
        router.message.register(self.help_message, F.text == HELP_BUTTON)
        router.message.register(self.instructions_message, Command("instructions"))
        router.message.register(self.instructions_message, F.text == INSTRUCTIONS_BUTTON)
        router.message.register(self.unknown_message)

        router.callback_query.register(self.home_callback, F.data == "ui2:h")
        router.callback_query.register(self.tariffs_callback, F.data == "ui2:t")
        router.callback_query.register(self.my_vpn_callback, F.data == "ui2:v")
        router.callback_query.register(self.instructions_callback, F.data == "ui2:i")
        router.callback_query.register(self.help_callback, F.data == "ui2:help")
        router.callback_query.register(self.back_callback, F.data.startswith("ui2:b:"))
        router.callback_query.register(self.retry_context_callback, F.data.startswith("ui2:retry:"))
        router.callback_query.register(self.select_tariff_callback, F.data.startswith("ui2:o:"))
        router.callback_query.register(self.retry_order_callback, F.data.startswith("ui2:r:"))

        router.callback_query.register(self.home_callback, F.data == "main_menu")
        router.callback_query.register(self.tariffs_callback, F.data == "tariffs")
        router.callback_query.register(self.my_vpn_callback, F.data == "dashboard")
        router.callback_query.register(self.help_callback, F.data == "help")
        router.callback_query.register(self.instructions_callback, F.data == "instructions")
        router.callback_query.register(self.legacy_buy_callback, F.data.startswith("buy:"))
        router.callback_query.register(self.stale_callback)
        return router

    async def start(self, message: Message) -> None:
        if await self._accept_message(message):
            await self._home_from_message(message)

    async def menu(self, message: Message) -> None:
        if await self._accept_message(message):
            await self._home_from_message(message)

    async def plans(self, message: Message) -> None:
        if await self._accept_message(message):
            await self._tariffs_from_message(message)

    async def status(self, message: Message) -> None:
        if await self._accept_message(message):
            await self._my_vpn_from_message(message)

    async def help_message(self, message: Message) -> None:
        if not await self._accept_message(message):
            return
        await self._presenter.send_message(message, HELP_TEXT, reply_markup=help_keyboard())

    async def instructions_message(self, message: Message) -> None:
        if not await self._accept_message(message):
            return
        await self._presenter.send_message(
            message,
            INSTRUCTIONS_TEXT,
            reply_markup=instructions_keyboard(),
        )

    async def unknown_message(self, message: Message) -> None:
        if not await self._accept_message(message):
            return
        await self._presenter.send_message(
            message,
            UNKNOWN_INPUT_TEXT,
            reply_markup=home_only_keyboard(),
        )

    async def home_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        await self._home_from_callback(callback)

    async def tariffs_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        await self._tariffs_from_callback(callback)

    async def my_vpn_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        await self._my_vpn_from_callback(callback)

    async def help_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        await self._presenter.render_callback(
            callback,
            HELP_TEXT,
            reply_markup=help_keyboard(),
            preserve_source=_is_artifact_source(callback),
        )

    async def instructions_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        await self._presenter.render_callback(
            callback,
            INSTRUCTIONS_TEXT,
            reply_markup=instructions_keyboard(),
            preserve_source=_is_artifact_source(callback),
        )

    async def back_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        route_code = (callback.data or "").removeprefix("ui2:b:")
        if route_code == "h":
            await self._home_from_callback(callback)
            return
        if route_code == "t":
            await self._tariffs_from_callback(callback)
            return
        await self._render_stale_callback(callback)

    async def retry_context_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        context_code = (callback.data or "").removeprefix("ui2:retry:")
        if context_code == "h":
            await self._home_from_callback(callback)
            return
        if context_code == "t":
            await self._tariffs_from_callback(callback)
            return
        if context_code == "v":
            await self._my_vpn_from_callback(callback)
            return
        await self._render_stale_callback(callback)

    async def select_tariff_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        parsed = _parse_order_callback(callback.data, prefix="ui2:o:")
        display_context = _intent_context_from_button(callback)
        if parsed is None or display_context is None:
            await self._render_stale_callback(callback)
            return
        tariff_id, intent_id = parsed
        if not await self._deduplicator.ensure_once(
            key=f"telegram:ui2:intent:{callback.from_user.id}:{tariff_id}:{intent_id}",
            ttl_seconds=86400,
        ):
            return
        self._order_intents[(callback.from_user.id, tariff_id, intent_id)] = display_context
        await self._create_order(callback, tariff_id=tariff_id, intent_id=intent_id)

    async def retry_order_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        parsed = _parse_order_callback(callback.data, prefix="ui2:r:")
        if parsed is None:
            await self._render_stale_callback(callback)
            return
        tariff_id, intent_id = parsed
        if (callback.from_user.id, tariff_id, intent_id) not in self._order_intents:
            await self._render_stale_callback(callback)
            return
        await self._create_order(callback, tariff_id=tariff_id, intent_id=intent_id)

    async def legacy_buy_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        target = await self._presenter.render_callback(
            callback,
            TARIFF_LOADING_TEXT,
            reply_markup=route_loading_keyboard(),
            preserve_source=_is_artifact_source(callback),
        )
        if target is None:
            return
        try:
            await self._backend.list_tariffs()
        except Exception as exc:
            logger.exception("Telegram UI v2 legacy tariff migration failed")
            await self._presenter.replace_message(
                target,
                _backend_failure_text(exc),
                reply_markup=backend_error_keyboard("t"),
            )
            return
        await self._presenter.replace_message(
            target,
            TARIFF_STALE_TEXT,
            reply_markup=tariff_refresh_keyboard(),
        )

    async def stale_callback(self, callback: CallbackQuery) -> None:
        if not await self._accept_callback(callback):
            return
        await self._render_stale_callback(callback)

    async def _home_from_message(self, message: Message) -> None:
        target = await self._presenter.send_message(message, HOME_LOADING_TEXT)
        try:
            await self._register_message_user(message)
            dashboard = await self._backend.get_dashboard(telegram_id=message.from_user.id)
        except Exception as exc:
            logger.exception("Telegram UI v2 home route failed")
            await self._presenter.replace_message(
                target,
                _backend_failure_text(exc),
                reply_markup=backend_error_keyboard("h"),
            )
            return
        text, keyboard = _home_screen(dashboard)
        await self._presenter.replace_message(target, text, reply_markup=keyboard)

    async def _home_from_callback(self, callback: CallbackQuery) -> None:
        target = await self._presenter.render_callback(
            callback,
            HOME_LOADING_TEXT,
            preserve_source=_is_artifact_source(callback),
        )
        if target is None:
            return
        try:
            await self._register_callback_user(callback)
            dashboard = await self._backend.get_dashboard(telegram_id=callback.from_user.id)
        except Exception as exc:
            logger.exception("Telegram UI v2 home callback route failed")
            await self._presenter.replace_message(
                target,
                _backend_failure_text(exc),
                reply_markup=backend_error_keyboard("h"),
            )
            return
        text, keyboard = _home_screen(dashboard)
        await self._presenter.replace_message(target, text, reply_markup=keyboard)

    async def _tariffs_from_message(self, message: Message) -> None:
        target = await self._presenter.send_message(
            message,
            TARIFF_LOADING_TEXT,
            reply_markup=route_loading_keyboard(),
        )
        try:
            await self._register_message_user(message)
            tariffs = _enabled_tariffs(await self._backend.list_tariffs())
        except Exception as exc:
            logger.exception("Telegram UI v2 tariff route failed")
            await self._presenter.replace_message(
                target,
                _backend_failure_text(exc),
                reply_markup=backend_error_keyboard("t"),
            )
            return
        await self._render_tariffs(target, telegram_id=message.from_user.id, tariffs=tariffs)

    async def _tariffs_from_callback(self, callback: CallbackQuery) -> None:
        target = await self._presenter.render_callback(
            callback,
            TARIFF_LOADING_TEXT,
            reply_markup=route_loading_keyboard(),
            preserve_source=_is_artifact_source(callback),
        )
        if target is None:
            return
        try:
            await self._register_callback_user(callback)
            tariffs = _enabled_tariffs(await self._backend.list_tariffs())
        except Exception as exc:
            logger.exception("Telegram UI v2 tariff callback route failed")
            await self._presenter.replace_message(
                target,
                _backend_failure_text(exc),
                reply_markup=backend_error_keyboard("t"),
            )
            return
        await self._render_tariffs(target, telegram_id=callback.from_user.id, tariffs=tariffs)

    async def _render_tariffs(
        self,
        target: Message,
        *,
        telegram_id: int,
        tariffs: list[TariffResponse],
    ) -> None:
        self._clear_order_intents(telegram_id)
        if not tariffs:
            await self._presenter.replace_message(
                target,
                TARIFF_EMPTY_TEXT,
                reply_markup=tariff_refresh_keyboard(),
            )
            return
        intent_ids = {tariff.tariff_id: _new_intent_id() for tariff in tariffs}
        await self._presenter.replace_message(
            target,
            _tariffs_text(tariffs),
            reply_markup=tariffs_keyboard(tariffs, intent_ids),
        )

    async def _my_vpn_from_message(self, message: Message) -> None:
        target = await self._presenter.send_message(
            message,
            MY_VPN_LOADING_TEXT,
            reply_markup=route_loading_keyboard(),
        )
        try:
            await self._register_message_user(message)
            dashboard = await self._backend.get_dashboard(telegram_id=message.from_user.id)
        except Exception as exc:
            logger.exception("Telegram UI v2 My VPN route failed")
            await self._presenter.replace_message(
                target,
                _backend_failure_text(exc),
                reply_markup=backend_error_keyboard("v"),
            )
            return
        text, keyboard = _my_vpn_screen(dashboard)
        await self._presenter.replace_message(target, text, reply_markup=keyboard)

    async def _my_vpn_from_callback(self, callback: CallbackQuery) -> None:
        target = await self._presenter.render_callback(
            callback,
            MY_VPN_LOADING_TEXT,
            reply_markup=route_loading_keyboard(),
            preserve_source=_is_artifact_source(callback),
        )
        if target is None:
            return
        try:
            await self._register_callback_user(callback)
            dashboard = await self._backend.get_dashboard(telegram_id=callback.from_user.id)
        except Exception as exc:
            logger.exception("Telegram UI v2 My VPN callback route failed")
            await self._presenter.replace_message(
                target,
                _backend_failure_text(exc),
                reply_markup=backend_error_keyboard("v"),
            )
            return
        text, keyboard = _my_vpn_screen(dashboard)
        await self._presenter.replace_message(target, text, reply_markup=keyboard)

    async def _create_order(
        self,
        callback: CallbackQuery,
        *,
        tariff_id: int,
        intent_id: str,
    ) -> None:
        key = (callback.from_user.id, tariff_id, intent_id)
        context = self._order_intents[key]
        target = await self._presenter.render_callback(
            callback,
            _order_creating_text(context),
            preserve_source=_is_artifact_source(callback),
        )
        if target is None:
            return
        try:
            await self._register_callback_user(callback)
            tariffs = _enabled_tariffs(await self._backend.list_tariffs())
            tariff = next((item for item in tariffs if item.tariff_id == tariff_id), None)
            if tariff is None:
                self._order_intents.pop(key, None)
                await self._presenter.replace_message(
                    target,
                    TARIFF_STALE_TEXT,
                    reply_markup=tariff_refresh_keyboard(),
                )
                return

            fresh_context = OrderIntentContext(
                tariff_name=tariff.tariff_name,
                price_display=_format_price(tariff.price_minor, tariff.currency),
            )
            if fresh_context != context:
                self._order_intents[key] = fresh_context
                context = fresh_context
                await self._presenter.replace_message(target, _order_creating_text(context))

            order = await self._backend.create_order(
                telegram_id=callback.from_user.id,
                tariff_id=tariff_id,
                provider=self._provider,
                idempotency_key=(
                    f"tg-ui2:{callback.from_user.id}:{tariff_id}:{intent_id}"
                ),
                username=callback.from_user.username,
                name_or_nick=_display_name(callback),
            )
        except Exception:
            logger.exception("Telegram UI v2 create order failed")
            await self._presenter.replace_message(
                target,
                ORDER_ERROR_TEXT,
                reply_markup=order_error_keyboard(tariff_id, intent_id),
            )
            return

        self._order_intents.pop(key, None)
        await self._render_payment_artifact(target, order)

    async def _render_payment_artifact(
        self,
        target: Message,
        order: BotCreateOrderResponse,
    ) -> None:
        payment_url = order.payment_url
        if not payment_url:
            await self._presenter.replace_message(
                target,
                _payment_url_unavailable_text(order),
                reply_markup=payment_unavailable_keyboard(),
            )
            return
        if _is_local_url(payment_url):
            if _backend_is_production(self._backend):
                await self._presenter.replace_message(
                    target,
                    _payment_url_unavailable_text(order),
                    reply_markup=payment_unavailable_keyboard(),
                )
                return
            await self._presenter.replace_message(
                target,
                _payment_dev_local_text(order),
                reply_markup=payment_dev_local_keyboard(),
            )
            return
        if not _is_valid_external_url(payment_url):
            await self._presenter.replace_message(
                target,
                _payment_url_unavailable_text(order),
                reply_markup=payment_unavailable_keyboard(),
            )
            return
        await self._presenter.replace_message(
            target,
            _payment_ready_text(order),
            reply_markup=payment_ready_keyboard(
                payment_url,
                order.amount_minor,
                order.currency,
            ),
        )

    async def _render_stale_callback(self, callback: CallbackQuery) -> None:
        await self._presenter.render_callback(
            callback,
            STALE_ACTION_TEXT,
            reply_markup=home_only_keyboard(),
            preserve_source=_is_artifact_source(callback),
        )

    async def _register_message_user(self, message: Message) -> None:
        await self._backend.register_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            name_or_nick=_display_name(message),
        )

    async def _register_callback_user(self, callback: CallbackQuery) -> None:
        await self._backend.register_user(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            name_or_nick=_display_name(callback),
        )

    async def _accept_message(self, message: Message) -> bool:
        return await self._deduplicator.ensure_once(
            key=f"telegram:message:{message.chat.id}:{message.message_id}",
            ttl_seconds=3600,
        )

    async def _accept_callback(self, callback: CallbackQuery) -> bool:
        # UI contract: acknowledge Telegram before Redis/backend/network work.
        await callback.answer()
        return await self._deduplicator.ensure_once(
            key=f"telegram:callback:{callback.from_user.id}:{callback.id}",
            ttl_seconds=3600,
        )

    def _clear_order_intents(self, telegram_id: int) -> None:
        stale_keys = [key for key in self._order_intents if key[0] == telegram_id]
        for key in stale_keys:
            self._order_intents.pop(key, None)


def setup_router(
    backend: BackendApiClient,
    deduplicator: RedisDeduplicator,
    provider: str,
) -> Router:
    return TelegramBotController(
        backend=backend,
        deduplicator=deduplicator,
        provider=provider,
    ).build_router()


def _home_screen(
    dashboard: BotDashboardResponse,
) -> tuple[str, InlineKeyboardMarkup]:
    if dashboard.subscription is None:
        return (
            "<b>Soter VPN</b>\n\n"
            "Активной подписки сейчас нет.\n\n"
            "Выберите тариф — после оплаты бот подготовит ключ подключения.",
            home_inactive_keyboard(),
        )
    expires_at = _format_datetime(dashboard.subscription.expires_at)
    vpn = dashboard.vpn_configuration
    if vpn is not None and vpn.uri:
        return (
            "<b>VPN активен</b>\n\n"
            f"Действует до: <b>{escape(expires_at)}</b>\n"
            "Ключ подключения готов.",
            home_active_ready_keyboard(),
        )
    return (
        "<b>Подписка активна</b>\n\n"
        f"Действует до: <b>{escape(expires_at)}</b>\n"
        "Ключ подключения пока не готов.",
        home_active_pending_keyboard(),
    )


def _my_vpn_screen(
    dashboard: BotDashboardResponse,
) -> tuple[str, InlineKeyboardMarkup]:
    if dashboard.subscription is None:
        return (
            "<b>Мой VPN</b>\n\n"
            "Активной подписки сейчас нет.\n\n"
            "Выберите тариф, чтобы получить ключ подключения.",
            my_vpn_inactive_keyboard(),
        )
    expires_at = _format_datetime(dashboard.subscription.expires_at)
    vpn = dashboard.vpn_configuration
    if vpn is not None and vpn.uri:
        return (
            "<b>VPN активен</b>\n\n"
            f"Действует до: <b>{escape(expires_at)}</b>\n\n"
            "<b>Ключ подключения</b>\n"
            f"<code>{escape(vpn.uri)}</code>\n\n"
            "Выберите устройство, чтобы открыть инструкцию по подключению.",
            my_vpn_ready_keyboard(),
        )
    return (
        "<b>Подписка активна</b>\n\n"
        f"Действует до: <b>{escape(expires_at)}</b>\n\n"
        "Ключ подключения пока не готов. Обновите статус позже.",
        my_vpn_pending_keyboard(),
    )


def _tariffs_text(tariffs: list[TariffResponse]) -> str:
    blocks: list[str] = []
    for tariff in tariffs:
        block_lines = [
            f"<b>{escape(tariff.tariff_name)}</b>",
            (
                f"{escape(_format_price(tariff.price_minor, tariff.currency))} · "
                f"{_format_period(tariff.period_days)}"
            ),
        ]
        if tariff.description and tariff.description.strip():
            block_lines.append(escape(tariff.description.strip()))
        blocks.append("\n".join(block_lines))
    return (
        "<b>Выберите тариф</b>\n\n"
        "После оплаты доступ активируется автоматически. "
        "Ключ подключения появится в разделе «Мой VPN».\n\n"
        + "\n\n".join(blocks)
    )


def _order_creating_text(context: OrderIntentContext) -> str:
    return (
        "<b>Создаю оплату</b>\n\n"
        f"Тариф: {escape(context.tariff_name)}\n"
        f"Сумма: <b>{escape(context.price_display)}</b>\n\n"
        "Подождите — запрос уже обрабатывается."
    )


def _payment_ready_text(order: BotCreateOrderResponse) -> str:
    return (
        f"<b>Заказ #{order.payment_order_id}</b>\n\n"
        f"Тариф: {escape(order.tariff_name)}\n"
        f"К оплате: <b>{escape(_format_price(order.amount_minor, order.currency))}</b>\n\n"
        "1. Откройте страницу оплаты.\n"
        "2. После оплаты вернитесь в бот и откройте «Мой VPN»."
    )


def _payment_url_unavailable_text(order: BotCreateOrderResponse) -> str:
    return (
        "<b>Не удалось открыть оплату</b>\n\n"
        f"Заказ #{order.payment_order_id} создан, но ссылка на оплату недоступна.\n\n"
        "Вернитесь к тарифам и попробуйте позже."
    )


def _payment_dev_local_text(order: BotCreateOrderResponse) -> str:
    return (
        "<b>Тестовая оплата</b>\n\n"
        "Локальная ссылка недоступна из Telegram-клиента:\n"
        f"<code>{escape(order.payment_url or '')}</code>\n\n"
        "Откройте её вручную в среде разработки."
    )


def _intent_context_from_button(callback: CallbackQuery) -> OrderIntentContext | None:
    source = callback.message
    if not isinstance(source, Message) or source.reply_markup is None:
        return None
    callback_data = callback.data or ""
    for row in source.reply_markup.inline_keyboard:
        for button in row:
            if button.callback_data != callback_data:
                continue
            prefix = "Выбрать · "
            if not button.text.startswith(prefix):
                return None
            body = button.text[len(prefix) :]
            parts = body.rsplit(" · ", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                return None
            return OrderIntentContext(tariff_name=parts[0], price_display=parts[1])
    return None


def _parse_order_callback(data: str | None, *, prefix: str) -> tuple[int, str] | None:
    if not data or not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 2:
        return None
    tariff_raw, intent_id = parts
    if not tariff_raw.isdigit() or len(intent_id) != 10:
        return None
    allowed = set(string.ascii_letters + string.digits + "_-")
    if any(char not in allowed for char in intent_id):
        return None
    return int(tariff_raw), intent_id


def _new_intent_id() -> str:
    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def _enabled_tariffs(tariffs: list[TariffResponse]) -> list[TariffResponse]:
    return [tariff for tariff in tariffs if tariff.is_enabled]


def _display_name(message_or_callback: Message | CallbackQuery) -> str:
    user = message_or_callback.from_user
    joined = " ".join(
        part for part in (user.first_name or "", user.last_name or "") if part
    ).strip()
    return joined or user.username or f"telegram-{user.id}"


def _format_price(price_minor: int, currency: str) -> str:
    amount = price_minor / 100
    return f"{int(amount)} {currency}" if amount.is_integer() else f"{amount:.2f} {currency}"


def _format_period(period_days: int) -> str:
    last_two = period_days % 100
    last = period_days % 10
    if 11 <= last_two <= 14:
        suffix = "дней"
    elif last == 1:
        suffix = "день"
    elif 2 <= last <= 4:
        suffix = "дня"
    else:
        suffix = "дней"
    return f"{period_days} {suffix}"


def _format_datetime(value: datetime) -> str:
    aware = (
        value
        if value.tzinfo is not None and value.utcoffset() is not None
        else value.replace(tzinfo=UTC)
    )
    offset = aware.strftime("%z")
    offset_display = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
    return f"{aware.strftime('%d.%m.%Y %H:%M')} UTC{offset_display}"


def _backend_failure_text(exc: Exception) -> str:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return NETWORK_ERROR_TEXT
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if 400 <= status_code < 500:
            return USER_RECOVERABLE_ERROR_TEXT
        return BACKEND_ERROR_TEXT
    return UNKNOWN_ERROR_TEXT


def _is_local_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def _is_valid_external_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not _is_local_url(url)


def _backend_is_production(backend: BackendApiClient) -> bool:
    settings: Any = getattr(backend, "_settings", None)
    return bool(getattr(settings, "is_production", True))


def _requires_new_message(message: Message) -> bool:
    return message.text is None


def _is_artifact_source(callback: CallbackQuery) -> bool:
    message = callback.message
    if not isinstance(message, Message):
        return False
    text = message.text or message.caption or ""
    artifact_headers = (
        "Заказ #",
        "🧾 Заказ #",
        "Не удалось открыть оплату",
        "Тестовая оплата",
        "Оплата ещё не подтверждена",
    )
    return any(text.startswith(header) for header in artifact_headers)
