from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from shop_bot.apps.bot.clients.backend_api import BackendApiClient
from shop_bot.infrastructure.redis.dedup import RedisDeduplicator
from shop_bot.schemas.bot import TariffResponse

router = Router()

BTN_CONNECT = "Подключиться"
BTN_TARIFFS = "Тарифы"
BTN_MY_KEYS = "Мои ключи"
BTN_HELP = "Помощь"


def setup_router(backend: BackendApiClient, deduplicator: RedisDeduplicator, provider: str) -> Router:
    async def dedup_message(message: Message) -> bool:
        return await deduplicator.ensure_once(
            key=f"telegram:message:{message.chat.id}:{message.message_id}",
            ttl_seconds=3600,
        )

    async def dedup_callback(callback: CallbackQuery) -> bool:
        return await deduplicator.ensure_once(
            key=f"telegram:callback:{callback.from_user.id}:{callback.id}",
            ttl_seconds=3600,
        )

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if not await dedup_message(message):
            return
        await backend.register_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            name_or_nick=_display_name(message),
        )
        await message.answer(
            "Главное меню. Выберите действие:",
            reply_markup=_main_menu_keyboard(),
        )

    @router.message(Command("plans"))
    async def plans(message: Message) -> None:
        if not await dedup_message(message):
            return
        tariffs = await backend.list_tariffs()
        await message.answer(
            "Доступные тарифы:",
            reply_markup=_tariffs_keyboard(tariffs),
        )

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        if not await dedup_message(message):
            return
        await _send_status(message, backend)

    @router.message(F.text == BTN_CONNECT)
    async def connect_button(message: Message) -> None:
        if not await dedup_message(message):
            return
        tariffs = await backend.list_tariffs()
        await message.answer(
            "Выберите тариф для подключения:",
            reply_markup=_tariffs_keyboard(tariffs),
        )

    @router.message(F.text == BTN_TARIFFS)
    async def tariffs_button(message: Message) -> None:
        if not await dedup_message(message):
            return
        tariffs = await backend.list_tariffs()
        await message.answer(
            "Доступные тарифы:",
            reply_markup=_tariffs_keyboard(tariffs),
        )

    @router.message(F.text == BTN_MY_KEYS)
    async def my_keys_button(message: Message) -> None:
        if not await dedup_message(message):
            return
        await _send_status(message, backend)

    @router.message(F.text == BTN_HELP)
    async def help_button(message: Message) -> None:
        if not await dedup_message(message):
            return
        await message.answer(
            "Доступные действия:\n"
            f"• {BTN_CONNECT} — выбрать тариф и оплатить\n"
            f"• {BTN_TARIFFS} — посмотреть доступные тарифы\n"
            f"• {BTN_MY_KEYS} — посмотреть активную подписку и ключ\n"
            f"• /status — статус подписки\n"
            f"• /plans — список тарифов"
        )

    @router.callback_query(F.data.startswith("buy:"))
    async def buy(callback: CallbackQuery) -> None:
        if not await dedup_callback(callback):
            await callback.answer("Duplicate click ignored", show_alert=False)
            return

        tariff_id = int(callback.data.split(":", 1)[1])
        order = await backend.create_order(
            telegram_id=callback.from_user.id,
            tariff_id=tariff_id,
            provider=provider,
            idempotency_key=f"cb:{callback.id}",
            username=callback.from_user.username,
            name_or_nick=_display_name(callback.message or callback),
        )

        payment_text = (
            f"Заказ #{order.payment_order_id}\n"
            f"Тариф: {order.tariff_name}\n"
            f"Сумма: {order.amount_minor} {order.currency}\n"
        )

        keyboard = None
        if order.payment_url:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть страницу оплаты", url=order.payment_url)]
                ]
            )
            payment_text += "Откройте страницу оплаты и завершите покупку."
        else:
            payment_text += "Платёжная ссылка не была получена от провайдера."

        await callback.message.answer(payment_text, reply_markup=keyboard)
        await callback.answer()

    return router


async def _send_status(message: Message, backend: BackendApiClient) -> None:
    dashboard = await backend.get_dashboard(telegram_id=message.from_user.id)
    if dashboard.subscription is None:
        await message.answer(
            "У вас пока нет активной подписки.\n"
            f"Нажмите «{BTN_CONNECT}», чтобы выбрать тариф.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    lines = [
        f"Тариф: {dashboard.subscription.tariff_name}",
        f"Активна до: {dashboard.subscription.expires_at.isoformat()}",
    ]

    if dashboard.vpn_configuration is not None:
        lines.extend(
            [
                f"Сервер: {dashboard.vpn_configuration.server_name}",
                f"Ключ: {dashboard.vpn_configuration.uri}",
            ]
        )
    else:
        lines.append("VPN-конфигурация ещё подготавливается. Попробуйте снова через несколько секунд.")

    await message.answer("\n".join(lines), reply_markup=_main_menu_keyboard())


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CONNECT)],
            [KeyboardButton(text=BTN_TARIFFS), KeyboardButton(text=BTN_MY_KEYS)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def _tariffs_keyboard(tariffs: list[TariffResponse]) -> InlineKeyboardMarkup:
    rows = []
    for tariff in tariffs:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{tariff.tariff_name} - {tariff.price_minor} {tariff.currency}",
                    callback_data=f"buy:{tariff.tariff_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _display_name(message_or_callback: Message | CallbackQuery) -> str:
    user = message_or_callback.from_user
    parts = [user.first_name or "", user.last_name or ""]
    joined = " ".join(part for part in parts if part).strip()
    return joined or user.username or f"telegram-{user.id}"