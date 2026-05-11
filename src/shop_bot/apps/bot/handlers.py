from __future__ import annotations

from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from shop_bot.apps.bot.clients.backend_api import BackendApiClient
from shop_bot.apps.bot.keyboards import (
    BACK_BUTTON,
    CONNECT_BUTTON,
    HELP_BUTTON,
    INSTRUCTIONS_BUTTON,
    KEYS_BUTTON,
    back_to_menu_keyboard,
    dashboard_keyboard,
    instructions_keyboard,
    main_reply_keyboard,
    payment_keyboard,
    tariffs_keyboard,
)
from shop_bot.infrastructure.redis.dedup import RedisDeduplicator
from shop_bot.schemas.bot import BotDashboardResponse


router = Router()

# В Docker картинка должна лежать здесь:
# C:\Users\Admin\Desktop\vless-shopbot-reworked-git\src\shop_bot\apps\bot\assets\menu.png
MENU_IMAGE_PATH = Path("/app/src/shop_bot/apps/bot/assets/menu.png")


HELP_TEXT = """🆘 <b>Помощь</b>

1. Нажмите <b>Подключиться</b> и выберите тариф.
2. Оплатите заказ по кнопке оплаты.
3. После оплаты нажмите <b>Мои ключи</b> или <b>Я оплатил / Проверить ключ</b>.
4. Если ключ ещё создаётся — подождите немного и проверьте снова.

Если возникла проблема:
— не пришёл ключ;
— не проходит оплата;
— не получается подключиться;
— приложение не импортирует ссылку;

напишите в поддержку.
"""


INSTRUCTIONS_TEXT = """📖 <b>Инструкции</b>

Выберите вашу платформу ниже.

Общая схема подключения:

1. Нажмите <b>Мои ключи</b>.
2. Скопируйте ссылку подписки.
3. Установите VPN-клиент для вашей платформы.
4. Импортируйте ссылку как <b>подписку</b>, а не как обычный VLESS-ключ.
5. Нажмите подключиться.

Если подписка не импортируется — используйте обычный VLESS-ключ ниже.
"""


def setup_router(
    backend: BackendApiClient,
    deduplicator: RedisDeduplicator,
    provider: str,
) -> Router:
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

    async def ensure_registered(message: Message) -> None:
        await backend.register_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            name_or_nick=_display_name(message),
        )

    async def ensure_registered_callback(callback: CallbackQuery) -> None:
        await backend.register_user(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            name_or_nick=_display_name(callback),
        )

    async def send_photo_or_text(
        message: Message,
        text: str | None = None,
        *,
        reply_markup=None,
        disable_web_page_preview: bool = True,
    ) -> None:
        if MENU_IMAGE_PATH.exists():
            if text:
                await message.answer_photo(
                    photo=FSInputFile(MENU_IMAGE_PATH),
                    caption=text,
                    reply_markup=reply_markup,
                )
            else:
                await message.answer_photo(
                    photo=FSInputFile(MENU_IMAGE_PATH),
                    reply_markup=reply_markup,
                )
            return

        await message.answer(
            text or "VPN Soter",
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )

    async def send_photo_or_text_callback(
        callback: CallbackQuery,
        text: str | None = None,
        *,
        reply_markup=None,
        disable_web_page_preview: bool = True,
    ) -> None:
        if MENU_IMAGE_PATH.exists():
            if text:
                await callback.message.answer_photo(
                    photo=FSInputFile(MENU_IMAGE_PATH),
                    caption=text,
                    reply_markup=reply_markup,
                )
            else:
                await callback.message.answer_photo(
                    photo=FSInputFile(MENU_IMAGE_PATH),
                    reply_markup=reply_markup,
                )
            return

        await callback.message.answer(
            text or "VPN Soter",
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )

    async def send_main_menu(message: Message) -> None:
        await ensure_registered(message)
        await send_photo_or_text(
            message,
            reply_markup=main_reply_keyboard(),
        )

    async def send_main_menu_callback(callback: CallbackQuery) -> None:
        await ensure_registered_callback(callback)
        await send_photo_or_text_callback(
            callback,
            reply_markup=main_reply_keyboard(),
        )

    async def send_tariffs(message: Message) -> None:
        await ensure_registered(message)

        tariffs = await backend.list_tariffs()

        if not tariffs:
            await message.answer(
                "Пока нет доступных тарифов. Проверьте тарифы в админке.",
                reply_markup=main_reply_keyboard(),
            )
            return

        text = (
            "🚀 <b>Выберите тариф</b>\n\n"
            "После оплаты бот автоматически создаст для вас VPN-ключ.\n"
            "Готовый ключ появится в разделе <b>Мои ключи</b>.\n\n"
            "Если вы впервые здесь, можете начать с пробного тарифа."
        )

        await send_photo_or_text(
            message,
            text,
            reply_markup=tariffs_keyboard(tariffs),
        )

    async def send_dashboard(message: Message) -> None:
        await ensure_registered(message)

        dashboard = await backend.get_dashboard(
            telegram_id=message.from_user.id,
        )

        await send_photo_or_text(
            message,
            _dashboard_text(dashboard),
            reply_markup=dashboard_keyboard(),
            disable_web_page_preview=True,
        )

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if not await dedup_message(message):
            return

        await send_main_menu(message)

    @router.message(Command("menu"))
    @router.message(F.text == BACK_BUTTON)
    async def menu(message: Message) -> None:
        if not await dedup_message(message):
            return

        await send_main_menu(message)

    @router.message(Command("plans"))
    @router.message(F.text == CONNECT_BUTTON)
    async def plans(message: Message) -> None:
        if not await dedup_message(message):
            return

        await send_tariffs(message)

    @router.message(Command("status"))
    @router.message(F.text == KEYS_BUTTON)
    async def status(message: Message) -> None:
        if not await dedup_message(message):
            return

        await send_dashboard(message)

    @router.message(Command("help"))
    @router.message(F.text == HELP_BUTTON)
    async def help_message(message: Message) -> None:
        if not await dedup_message(message):
            return

        await ensure_registered(message)

        await message.answer(
            HELP_TEXT,
            reply_markup=back_to_menu_keyboard(),
        )

    @router.message(Command("instructions"))
    @router.message(F.text == INSTRUCTIONS_BUTTON)
    async def instructions_message(message: Message) -> None:
        if not await dedup_message(message):
            return

        await ensure_registered(message)

        await message.answer(
            INSTRUCTIONS_TEXT,
            reply_markup=instructions_keyboard(),
            disable_web_page_preview=True,
        )

    @router.callback_query(F.data == "main_menu")
    async def main_menu_callback(callback: CallbackQuery) -> None:
        if not await dedup_callback(callback):
            await callback.answer("Уже обрабатываю", show_alert=False)
            return

        await send_main_menu_callback(callback)
        await callback.answer()

    @router.callback_query(F.data == "tariffs")
    async def tariffs_callback(callback: CallbackQuery) -> None:
        if not await dedup_callback(callback):
            await callback.answer("Уже обрабатываю", show_alert=False)
            return

        await ensure_registered_callback(callback)

        tariffs = await backend.list_tariffs()

        if not tariffs:
            await callback.message.answer(
                "Пока нет доступных тарифов. Проверьте тарифы в админке.",
                reply_markup=main_reply_keyboard(),
            )
            await callback.answer()
            return

        text = (
            "🚀 <b>Выберите тариф</b>\n\n"
            "После оплаты бот автоматически создаст для вас VPN-ключ.\n"
            "Готовый ключ появится в разделе <b>Мои ключи</b>."
        )

        await send_photo_or_text_callback(
            callback,
            text,
            reply_markup=tariffs_keyboard(tariffs),
        )

        await callback.answer()

    @router.callback_query(F.data == "dashboard")
    async def dashboard_callback(callback: CallbackQuery) -> None:
        if not await dedup_callback(callback):
            await callback.answer("Уже обрабатываю", show_alert=False)
            return

        await ensure_registered_callback(callback)

        dashboard = await backend.get_dashboard(
            telegram_id=callback.from_user.id,
        )

        await send_photo_or_text_callback(
            callback,
            _dashboard_text(dashboard),
            reply_markup=dashboard_keyboard(),
            disable_web_page_preview=True,
        )

        await callback.answer()

    @router.callback_query(F.data == "help")
    async def help_callback(callback: CallbackQuery) -> None:
        if not await dedup_callback(callback):
            await callback.answer("Уже обрабатываю", show_alert=False)
            return

        await callback.message.answer(
            HELP_TEXT,
            reply_markup=back_to_menu_keyboard(),
        )

        await callback.answer()

    @router.callback_query(F.data == "instructions")
    async def instructions_callback(callback: CallbackQuery) -> None:
        if not await dedup_callback(callback):
            await callback.answer("Уже обрабатываю", show_alert=False)
            return

        await callback.message.answer(
            INSTRUCTIONS_TEXT,
            reply_markup=instructions_keyboard(),
            disable_web_page_preview=True,
        )

        await callback.answer()

    @router.callback_query(F.data.startswith("buy:"))
    async def buy(callback: CallbackQuery) -> None:
        if not await dedup_callback(callback):
            await callback.answer("Повторный клик проигнорирован", show_alert=False)
            return

        await ensure_registered_callback(callback)

        tariff_id = int(callback.data.split(":", 1)[1])

        order = await backend.create_order(
            telegram_id=callback.from_user.id,
            tariff_id=tariff_id,
            provider=provider,
            idempotency_key=f"cb:{callback.id}",
            username=callback.from_user.username,
            name_or_nick=_display_name(callback),
        )

        payment_text = (
            f"🧾 <b>Заказ #{order.payment_order_id}</b>\n\n"
            f"Тариф: <b>{order.tariff_name}</b>\n"
            f"Сумма: <b>{_format_price(order.amount_minor, order.currency)}</b>\n\n"
        )

        if order.payment_url:
            if _is_local_url(order.payment_url):
                payment_text += (
                    "Сейчас включена тестовая локальная оплата.\n\n"
                    "Telegram не может открыть localhost-ссылку из кнопки.\n"
                    "Для проверки откройте ссылку вручную на компьютере:\n\n"
                    f"<code>{order.payment_url}</code>\n\n"
                    "После тестовой оплаты нажмите <b>Я оплатил / Проверить ключ</b>."
                )
            else:
                payment_text += (
                    "Нажмите кнопку оплаты. После успешной оплаты вернитесь в бот "
                    "и нажмите <b>Я оплатил / Проверить ключ</b>."
                )
        else:
            payment_text += (
                "Платёжная ссылка не вернулась от провайдера. "
                "Проверьте настройки платежей."
            )

        await callback.message.answer(
            payment_text,
            reply_markup=payment_keyboard(order.payment_url),
            disable_web_page_preview=True,
        )

        await callback.answer()

    return router


def _dashboard_text(dashboard: BotDashboardResponse) -> str:
    if dashboard.user is None:
        return "Сначала нажмите /start, чтобы зарегистрироваться."

    if dashboard.subscription is None:
        return (
            "🔑 <b>Мои ключи</b>\n\n"
            "Активной подписки пока нет.\n\n"
            "Нажмите <b>Подключиться</b>, выберите тариф и оплатите заказ."
        )

    lines = [
        "🔑 <b>Мои ключи</b>",
        "",
        f"Тариф: <b>{dashboard.subscription.tariff_name}</b>",
        f"Активна до: <b>{dashboard.subscription.expires_at.strftime('%d.%m.%Y %H:%M')}</b>",
    ]

    if dashboard.vpn_configuration is None:
        lines.extend(
            [
                "",
                "⏳ VPN-конфигурация ещё создаётся.",
                "Нажмите <b>Мои ключи</b> через несколько секунд.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            f"Сервер: <b>{dashboard.vpn_configuration.server_name}</b>",
            f"Статус: <b>{dashboard.vpn_configuration.status}</b>",
        ]
    )

    subscription_url = dashboard.vpn_configuration.subscription_url

    if subscription_url:
        lines.extend(
            [
                "",
                "📲 <b>Ваша VPN-ссылка:</b>",
"",
"Нажмите на ссылку ниже, чтобы скопировать:",
f"<pre><code>{subscription_url}</code></pre>",
                "",
                "📖 Если не знаете, как подключиться — откройте раздел <b>Инструкции</b>.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "⏳ Ссылка подписки ещё не создана.",
            ]
        )

    return "\n".join(lines)
    lines.extend(
        [
            f"Сервер: <b>{dashboard.vpn_configuration.server_name}</b>",
            f"Статус: <b>{dashboard.vpn_configuration.status}</b>",
        ]
    )

    if dashboard.vpn_configuration.subscription_url:
        lines.extend(
            [
                "",
                "📲 <b>Ссылка подписки для V2RayTun / Happ / v2rayN:</b>",
                f"<code>{dashboard.vpn_configuration.subscription_url}</code>",
                "",
                "Добавляйте её в приложении как <b>подписку</b>, а не как обычный VLESS-ключ.",
                "Внутри будет профиль: <b>🇩🇪 Основной</b>",
            ]
        )

    if dashboard.vpn_configuration.uri:
        lines.extend(
            [
                "",
                "🔗 <b>Обычный VLESS-ключ, если подписка не импортируется:</b>",
                f"<code>{dashboard.vpn_configuration.uri}</code>",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "⏳ Клиент создан, но URI ещё не сформирован.",
                "Проверьте node-agent и нажмите <b>Мои ключи</b> ещё раз.",
            ]
        )

    lines.extend(
        [
            "",
            "📖 Если не знаете, как подключиться — откройте раздел <b>Инструкции</b>.",
        ]
    )

    return "\n".join(lines)


def _display_name(message_or_callback: Message | CallbackQuery) -> str:
    user = message_or_callback.from_user
    parts = [user.first_name or "", user.last_name or ""]
    joined = " ".join(part for part in parts if part).strip()

    return joined or user.username or f"telegram-{user.id}"


def _format_price(price_minor: int, currency: str) -> str:
    amount = price_minor / 100

    if amount.is_integer():
        return f"{int(amount)} {currency}"

    return f"{amount:.2f} {currency}"


def _is_local_url(url: str) -> bool:
    return (
        url.startswith("http://localhost")
        or url.startswith("https://localhost")
        or url.startswith("http://127.0.0.1")
        or url.startswith("https://127.0.0.1")
    )