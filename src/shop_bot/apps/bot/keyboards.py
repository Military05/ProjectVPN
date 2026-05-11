from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from shop_bot.schemas.bot import TariffResponse


CONNECT_BUTTON = "🚀 Подключиться"
KEYS_BUTTON = "🔑 Мои ключи"
HELP_BUTTON = "🆘 Помощь"
INSTRUCTIONS_BUTTON = "📖 Инструкции"
BACK_BUTTON = "⬅️ Назад"


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CONNECT_BUTTON)],
            [KeyboardButton(text=KEYS_BUTTON)],
            [KeyboardButton(text=INSTRUCTIONS_BUTTON)],
            [KeyboardButton(text=HELP_BUTTON)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def menu_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CONNECT_BUTTON)],
            [KeyboardButton(text=KEYS_BUTTON)],
            [KeyboardButton(text=INSTRUCTIONS_BUTTON)],
            [KeyboardButton(text=HELP_BUTTON)],
            [KeyboardButton(text=BACK_BUTTON)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def tariffs_keyboard(tariffs: list[TariffResponse]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for tariff in tariffs:
        price = _format_price(tariff.price_minor, tariff.currency)
        period = f"{tariff.period_days} дней"
        label = f"{tariff.tariff_name} — {price} / {period}"

        if getattr(tariff, "traffic_limit_gb", None):
            label += f" · {tariff.traffic_limit_gb} ГБ"

        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"buy:{tariff.tariff_id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text=KEYS_BUTTON, callback_data="dashboard")])
    rows.append([InlineKeyboardButton(text=INSTRUCTIONS_BUTTON, callback_data="instructions")])
    rows.append([InlineKeyboardButton(text=BACK_BUTTON, callback_data="main_menu")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_keyboard(payment_url: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if payment_url and not _is_local_url(payment_url):
        rows.append([InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)])

    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Я оплатил / Проверить ключ",
                callback_data="dashboard",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text=BACK_BUTTON, callback_data="main_menu")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Подключиться / Продлить", callback_data="tariffs")],
            [InlineKeyboardButton(text=INSTRUCTIONS_BUTTON, callback_data="instructions")],
            [InlineKeyboardButton(text=HELP_BUTTON, callback_data="help")],
            [InlineKeyboardButton(text=BACK_BUTTON, callback_data="main_menu")],
        ]
    )


def instructions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Android",
                    url="https://telegra.ph/Instrukciya-Android-11-09",
                ),
                InlineKeyboardButton(
                    text="📱 iOS",
                    url="https://telegra.ph/Instrukciya-iOS-11-09",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💻 Windows",
                    url="https://telegra.ph/Instrukciya-Windows-11-09",
                ),
                InlineKeyboardButton(
                    text="🐧 Linux",
                    url="https://telegra.ph/Instrukciya-Linux-11-09",
                ),
            ],
            [InlineKeyboardButton(text=KEYS_BUTTON, callback_data="dashboard")],
            [InlineKeyboardButton(text=BACK_BUTTON, callback_data="main_menu")],
        ]
    )


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=INSTRUCTIONS_BUTTON, callback_data="instructions")],
            [InlineKeyboardButton(text=KEYS_BUTTON, callback_data="dashboard")],
            [InlineKeyboardButton(text=BACK_BUTTON, callback_data="main_menu")],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BACK_BUTTON, callback_data="main_menu")]
        ]
    )


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

