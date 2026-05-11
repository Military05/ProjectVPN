import secrets

from fastapi import Header, HTTPException, status

from shop_bot.core.config import get_settings


async def require_internal_api_key(x_internal_api_key: str = Header(default="")) -> None:
    settings = get_settings()
    if not secrets.compare_digest(x_internal_api_key, settings.internal_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal API key")


async def require_admin_token(x_admin_token: str = Header(default="")) -> None:
    settings = get_settings()
    if not secrets.compare_digest(x_admin_token, settings.admin_api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
