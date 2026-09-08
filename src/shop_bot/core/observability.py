from __future__ import annotations

import logging
from typing import Any

from shop_bot.core.config import Settings

logger = logging.getLogger(__name__)


def setup_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("Sentry SDK is unavailable; error reporting is disabled")
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.2,
    )


def setup_tracing(settings: Settings, service_name: str) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OpenTelemetry runtime is incomplete; tracing is disabled")
        return

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    LoggingInstrumentor().instrument(set_logging_format=True)


def instrument_fastapi(app: Any) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        logger.warning("FastAPI tracing instrumentation is unavailable")
    else:
        FastAPIInstrumentor.instrument_app(app)

    try:
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError:
        logger.warning("Prometheus instrumentation is unavailable")
    else:
        Instrumentator().instrument(app).expose(
            app,
            include_in_schema=False,
            endpoint="/metrics",
        )
