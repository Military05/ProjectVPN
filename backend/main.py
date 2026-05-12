import base64
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from models import servers

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
RAYTAN_KEY = os.getenv("RAYTAN_KEY")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

PUBLIC_SUBSCRIPTION_BASE_URL = os.getenv(
    "PUBLIC_SUBSCRIPTION_BASE_URL",
    "http://127.0.0.1:8080",
).rstrip("/")

SUBSCRIPTION_TOKEN = os.getenv("SUBSCRIPTION_TOKEN", "soter")

app = FastAPI(title="SoterVPN Subscription API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def build_raw_subscription() -> str:
    """
    Возвращает список VLESS-ссылок.
    Каждая строка — отдельный канал для Raytan.
    """
    links = []

    for server in servers:
        link = server.get("link", "").strip()

        if not link:
            continue

        links.append(link)

    return "\n".join(links)


def build_base64_subscription() -> str:
    """
    Классический формат subscription:
    base64 от списка VLESS-ссылок через новую строку.
    """
    raw_subscription = build_raw_subscription()
    encoded = base64.b64encode(raw_subscription.encode("utf-8")).decode("utf-8")
    return encoded


def get_subscription_url() -> str:
    """
    Одна ссылка, которую пользователь вставляет в Raytan.
    """
    return f"{PUBLIC_SUBSCRIPTION_BASE_URL}/sub/{SUBSCRIPTION_TOKEN}"


def get_links_for_bot() -> str:
    """
    Текст, который Telegram-бот должен отправлять пользователю после оплаты.
    """
    return (
        "Ваш ключ SoterVPN:\n\n"
        f"{get_subscription_url()}\n\n"
        "Скопируйте эту ссылку и добавьте её в Raytan как подписку."
    )


def handle_connect(chat_id, bot):
    """
    Отправка пользователю одной subscription-ссылки.
    """
    bot.send_message(chat_id, get_links_for_bot())


@app.get("/")
async def root():
    return {
        "service": "SoterVPN Subscription API",
        "channels": len(servers),
        "subscription_url": get_subscription_url(),
    }


@app.get("/channels")
async def get_channels():
    """
    Проверка каналов в JSON.
    """
    return {
        "count": len(servers),
        "channels": [
            {
                "id": index,
                "country": server.get("country"),
                "flag": server.get("flag"),
                "name": server.get("name"),
            }
            for index, server in enumerate(servers, start=1)
        ],
    }


@app.get("/sub/{token}")
async def get_subscription(token: str):
    """
    Основная subscription-ссылка для Raytan.
    Пользователь вставляет именно этот URL.
    Внутри URL отдаёт 15 каналов в base64.

    Важно:
    Здесь специально НЕ отдаём Subscription-Userinfo,
    чтобы приложение не показывало лимит 500 GB.
    """
    if token != SUBSCRIPTION_TOKEN:
        return Response(
            content="Invalid subscription token",
            status_code=403,
            media_type="text/plain; charset=utf-8",
        )

    return Response(
        content=build_base64_subscription(),
        media_type="text/plain; charset=utf-8",
        headers={
            "Profile-Title": "SoterVPN",
        },
    )


@app.get("/sub/{token}/raw")
async def get_raw_subscription(token: str):
    """
    Сырой список VLESS-ссылок.
    Это только для проверки.
    Raytan лучше давать обычный /sub/soter.
    """
    if token != SUBSCRIPTION_TOKEN:
        return Response(
            content="Invalid subscription token",
            status_code=403,
            media_type="text/plain; charset=utf-8",
        )

    return Response(
        content=build_raw_subscription(),
        media_type="text/plain; charset=utf-8",
    )


if __name__ == "__main__":
    print("=== SoterVPN Subscription ===")
    print()
    print(f"Количество каналов: {len(servers)}")
    print()
    print("Одна ссылка для Raytan:")
    print(get_subscription_url())
    print()
    print("Каналы внутри подписки:")
    print()

    for index, server in enumerate(servers, start=1):
        print(f"{index}. {server['name']}")

    print()
    print("Для запуска API используй команду:")
    print(f"uvicorn main:app --host {HOST} --port {PORT}")