# backend/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.channels import CHANNELS

app = FastAPI(title="VPN Multi-node Interface")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/channels")
async def get_channels():
    """Возвращает список 15 каналов с серверами и флагами"""
    return CHANNELS

@app.get("/v2raytan-link")
async def get_v2raytan_link():
    """
    Формирует ссылку для V2RayTan на основе всех каналов.
    Пример формата: vless://user@server:port?security=tls&encryption=auto#ChannelName
    """
    base = "vless://demo-user@{server}:{port}?security=tls&encryption=auto#{name}"
    links = [base.format(server=c["server"], port=c["port"], name=c["name"]) for c in CHANNELS]
    return {"v2raytan_link": "\n".join(links)}