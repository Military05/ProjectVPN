from __future__ import annotations

from collections.abc import Mapping

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from shop_bot.schemas.bot import TariffResponse


# Historical ReplyKeyboard texts are migration aliases only. UI v2 never renders ReplyKeyboard.
CONNECT_BUTTON = "🚀 Подключиться"
KEYS_BUTTON = "🔑 Мои ключи"
HELP_BUTTON = "🆘 Помощь"
INSTRUCTIONS_BUTTON = "📖 Инструкции"
BACK_BUTTON = "⬅️ Назад"

ANDROID_URL = "https://telegra.ph/Instrukciya-Android-11-09"
IOS_URL = "https://telegra.ph/Instrukciya-iOS-11-09"
WINDOWS_URL = "https://telegra.ph/Instrukciya-Windows-11-09"
LINUX_URL = "https://telegra.ph/Instrukciya-Linux-11-09"


def home_inactive_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Выбрать тариф", "ui2:t")],
            [_callback_button("Мой VPN", "ui2:v")],
            [_callback_button("Инструкции", "ui2:i")],
            [_callback_button("Помощь", "ui2:help")],
        ]
    )


def home_active_ready_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Мой VPN", "ui2:v")],
            [_callback_button("Продлить подписку", "ui2:t")],
            [_callback_button("Инструкции", "ui2:i")],
            [_callback_button("Помощь", "ui2:help")],
        ]
    )


def home_active_pending_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Обновить статус", "ui2:v")],
            [_callback_button("Продлить подписку", "ui2:t")],
            [_callback_button("Инструкции", "ui2:i")],
            [_callback_button("Помощь", "ui2:help")],
        ]
    )


def route_loading_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_callback_button("Назад", "ui2:b:h")]])


def tariffs_keyboard(
    tariffs: list[TariffResponse],
    intent_ids: Mapping[int, str],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for tariff in tariffs:
        intent_id = intent_ids[tariff.tariff_id]
        rows.append(
            [
                _callback_button(
                    (
                        f"Выбрать · {tariff.tariff_name} · "
                        f"{_format_price(tariff.price_minor, tariff.currency)}"
                    ),
                    f"ui2:o:{tariff.tariff_id}:{intent_id}",
                )
            ]
        )
    rows.append([_callback_button("Назад", "ui2:b:h")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tariff_refresh_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Обновить тарифы", "ui2:t")],
            [_callback_button("Назад", "ui2:b:h")],
        ]
    )


def order_error_keyboard(tariff_id: int, intent_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Повторить", f"ui2:r:{tariff_id}:{intent_id}")],
            [_callback_button("Вернуться к тарифам", "ui2:t")],
            _back_and_home_row("t"),
        ]
    )


def payment_ready_keyboard(
    payment_url: str,
    amount_minor: int,
    currency: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Оплатить {_format_price(amount_minor, currency)}",
                    url=payment_url,
                )
            ],
            [_callback_button("Открыть мой VPN", "ui2:v")],
            _back_and_home_row("t"),
        ]
    )


def payment_unavailable_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Вернуться к тарифам", "ui2:t")],
            _back_and_home_row("t"),
        ]
    )


def payment_dev_local_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Вернуться к тарифам", "ui2:t")],
            _back_and_home_row("t"),
        ]
    )


def my_vpn_inactive_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Выбрать тариф", "ui2:t")],
            [_callback_button("Инструкции", "ui2:i")],
            [_callback_button("Назад", "ui2:b:h")],
        ]
    )


def my_vpn_pending_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Обновить статус", "ui2:v")],
            [_callback_button("Помощь", "ui2:help")],
            [_callback_button("Назад", "ui2:b:h")],
        ]
    )


def my_vpn_ready_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _platform_row("Android", ANDROID_URL, "iOS", IOS_URL),
            _platform_row("Windows", WINDOWS_URL, "Linux", LINUX_URL),
            [_callback_button("Продлить подписку", "ui2:t")],
            [_callback_button("Назад", "ui2:b:h")],
        ]
    )


def instructions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _platform_row("Android", ANDROID_URL, "iOS", IOS_URL),
            _platform_row("Windows", WINDOWS_URL, "Linux", LINUX_URL),
            [_callback_button("Мой VPN", "ui2:v")],
            [_callback_button("Назад", "ui2:b:h")],
        ]
    )


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Мой VPN", "ui2:v")],
            [_callback_button("Инструкции", "ui2:i")],
            [_callback_button("Назад", "ui2:b:h")],
        ]
    )


def backend_error_keyboard(context_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_callback_button("Повторить", f"ui2:retry:{context_code}")],
            [_callback_button("Главное меню", "ui2:h")],
        ]
    )


def home_only_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_callback_button("Главное меню", "ui2:h")]])


def _callback_button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def _back_and_home_row(back_route_code: str) -> list[InlineKeyboardButton]:
    return [
        _callback_button("Назад", f"ui2:b:{back_route_code}"),
        _callback_button("Главное меню", "ui2:h"),
    ]


def _platform_row(
    first_label: str,
    first_url: str,
    second_label: str,
    second_url: str,
) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text=first_label, url=first_url),
        InlineKeyboardButton(text=second_label, url=second_url),
    ]


def _format_price(price_minor: int, currency: str) -> str:
    amount = price_minor / 100
    if amount.is_integer():
        return f"{int(amount)} {currency}"
    return f"{amount:.2f} {currency}"
