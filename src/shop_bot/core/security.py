import secrets

from fastapi import Header, HTTPException, Request, status


async def require_internal_api_key(request: Request, x_internal_api_key: str = Header(default="")) -> None:
    settings = request.app.state.settings
    if not secrets.compare_digest(x_internal_api_key, settings.internal_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal API key")


async def require_admin_token(request: Request, x_admin_token: str = Header(default="")) -> None:
    settings = request.app.state.settings
    if not secrets.compare_digest(x_admin_token, settings.admin_api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token")
