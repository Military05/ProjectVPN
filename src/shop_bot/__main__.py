from shop_bot.apps.api.__main__ import main as api_main
from shop_bot.apps.bot.__main__ import main as bot_main
from shop_bot.apps.node_agent.__main__ import main as node_agent_main
from shop_bot.apps.worker.__main__ import main as worker_main
from shop_bot.core.config import get_settings


def main() -> None:
    settings = get_settings()
    mode = settings.service_mode
    if mode == "api":
        api_main()
        return
    if mode == "bot":
        bot_main()
        return
    if mode == "worker":
        worker_main()
        return
    if mode == "node_agent":
        node_agent_main()
        return
    raise ValueError(f"Unsupported service mode: {mode}")


if __name__ == "__main__":
    main()
