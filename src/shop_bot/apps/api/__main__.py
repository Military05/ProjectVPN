from __future__ import annotations

import uvicorn

from shop_bot.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "shop_bot.apps.api.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
