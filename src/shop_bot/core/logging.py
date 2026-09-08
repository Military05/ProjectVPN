from __future__ import annotations

import logging
import sys

from shop_bot.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure structured logging when structlog is installed.

    The standard-library fallback keeps management commands and route discovery
    usable in reduced environments while production installations still use the
    declared structlog dependency.
    """

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    try:
        import structlog
    except ImportError:
        logging.getLogger(__name__).warning(
            "structlog is unavailable; standard logging configuration is active"
        )
        return

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=shared_processors + [structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
