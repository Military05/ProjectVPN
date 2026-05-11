from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parents[1] / "static" / "admin"
ASSETS_DIR = STATIC_DIR / "assets"
INDEX_FILE = STATIC_DIR / "index.html"

router = APIRouter(tags=["admin-ui"])


@router.get("/admin-ui", include_in_schema=False)
async def admin_ui_root() -> FileResponse:
    return FileResponse(INDEX_FILE)


@router.get("/admin-ui/", include_in_schema=False)
async def admin_ui_index() -> FileResponse:
    return FileResponse(INDEX_FILE)


def register_admin_ui(app: FastAPI) -> None:
    app.mount(
        "/admin-ui/assets",
        StaticFiles(directory=str(ASSETS_DIR)),
        name="admin-ui-assets",
    )
    app.include_router(router)
