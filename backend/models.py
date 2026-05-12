from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, DateTime
from urllib.parse import quote

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime)


# =========================
# REAL VPS LINKS
# =========================

NETHERLANDS_BASE_LINK = (
    "vless://6bbcd0e8-d316-46df-9a96-0521c32e1c98@82.24.195.175:20530"
    "?type=tcp&encryption=none&security=reality"
    "&pbk=G-dNWdrh7SsSos0u_ouorPG42IPOpKRfU3cu16NIIXg"
    "&fp=chrome"
    "&sni=www.microsoft.com"
    "&sid=cf52742614470225"
    "&spx=%2F"
    "&flow=xtls-rprx-vision"
)

GERMANY_BASE_LINK = (
    "vless://c1d5b640-8431-42de-8ad2-bbdbb04c8440@194.247.186.183:443"
    "?type=tcp&encryption=none&security=reality"
    "&pbk=9niJBVyFiiMXyQuzLTE7wARWUl2x2DNmaL5BSCT2cUI"
    "&fp=chrome"
    "&sni=www.cloudflare.com"
    "&sid=09"
    "&spx=%2F"
)


def build_vless_link(base_link: str, display_name: str) -> str:
    """
    Делает отдельный канал для Raytan.
    Один и тот же сервер может быть показан как разные каналы,
    если у каждой ссылки разное имя после #.
    """
    clean_base = base_link.split("#", 1)[0]
    encoded_name = quote(display_name, safe="")
    return f"{clean_base}#{encoded_name}"


# =========================
# CHANNELS FOR RAYTAN
# =========================

channels = [
    # =========================
    # NETHERLANDS — FIRST 7 CHANNELS
    # =========================

    {
        "country": "NL",
        "flag": "🇳🇱",
        "name": "🇳🇱 ⚡ Основной",
        "link": build_vless_link(NETHERLANDS_BASE_LINK, "🇳🇱 ⚡ Основной"),
    },
    {
        "country": "NL",
        "flag": "🇳🇱",
        "name": "🇳🇱 🔧 Резервный #1",
        "link": build_vless_link(NETHERLANDS_BASE_LINK, "🇳🇱 🔧 Резервный #1"),
    },
    {
        "country": "NL",
        "flag": "🇳🇱",
        "name": "🇳🇱 🔧 Резервный #2",
        "link": build_vless_link(NETHERLANDS_BASE_LINK, "🇳🇱 🔧 Резервный #2"),
    },
    {
        "country": "NL",
        "flag": "🇳🇱",
        "name": "🇳🇱 🔧 Резервный #3",
        "link": build_vless_link(NETHERLANDS_BASE_LINK, "🇳🇱 🔧 Резервный #3"),
    },
    {
        "country": "NL",
        "flag": "🇳🇱",
        "name": "🇳🇱 🏠 Домашний Fix #1 💊",
        "link": build_vless_link(NETHERLANDS_BASE_LINK, "🇳🇱 🏠 Домашний Fix #1 💊"),
    },
    {
        "country": "NL",
        "flag": "🇳🇱",
        "name": "🇳🇱 🏠 Домашний Fix #2 💊",
        "link": build_vless_link(NETHERLANDS_BASE_LINK, "🇳🇱 🏠 Домашний Fix #2 💊"),
    },
    {
        "country": "NL",
        "flag": "🇳🇱",
        "name": "🇳🇱 #1 LTE 📱 (x5 трафик)",
        "link": build_vless_link(NETHERLANDS_BASE_LINK, "🇳🇱 #1 LTE 📱 (x5 трафик)"),
    },

    # =========================
    # GERMANY — NEXT 8 CHANNELS
    # =========================

    {
        "country": "DE",
        "flag": "🇩🇪",
        "name": "🇩🇪 #2 LTE 📱",
        "link": build_vless_link(GERMANY_BASE_LINK, "🇩🇪 #2 LTE 📱"),
    },
    {
        "country": "DE",
        "flag": "🇩🇪",
        "name": "🇩🇪 #3 LTE 📱 (x5 трафик)",
        "link": build_vless_link(GERMANY_BASE_LINK, "🇩🇪 #3 LTE 📱 (x5 трафик)"),
    },
    {
        "country": "DE",
        "flag": "🇩🇪",
        "name": "🇩🇪 #4 LTE 📱",
        "link": build_vless_link(GERMANY_BASE_LINK, "🇩🇪 #4 LTE 📱"),
    },
    {
        "country": "DE",
        "flag": "🇩🇪",
        "name": "🇩🇪 #5 LTE 📱",
        "link": build_vless_link(GERMANY_BASE_LINK, "🇩🇪 #5 LTE 📱"),
    },
    {
        "country": "DE",
        "flag": "🇩🇪",
        "name": "🇩🇪 #6 LTE 📱",
        "link": build_vless_link(GERMANY_BASE_LINK, "🇩🇪 #6 LTE 📱"),
    },
    {
        "country": "DE",
        "flag": "🇩🇪",
        "name": "🇩🇪 #7 LTE 📱",
        "link": build_vless_link(GERMANY_BASE_LINK, "🇩🇪 #7 LTE 📱"),
    },
    {
        "country": "DE",
        "flag": "🇩🇪",
        "name": "🇩🇪 #8 LTE 📱",
        "link": build_vless_link(GERMANY_BASE_LINK, "🇩🇪 #8 LTE 📱"),
    },
    {
        "country": "DE",
        "flag": "🇩🇪",
        "name": "🇩🇪 #9 LTE 📱",
        "link": build_vless_link(GERMANY_BASE_LINK, "🇩🇪 #9 LTE 📱"),
    },
]


# Для совместимости с main.py оставляем имя servers.
servers = channels