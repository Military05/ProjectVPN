FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/src:/app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir \
    opentelemetry-instrumentation-logging \
    opentelemetry-instrumentation-fastapi \
    opentelemetry-instrumentation-asgi \
    opentelemetry-instrumentation-httpx \
    opentelemetry-instrumentation-redis \
    opentelemetry-instrumentation-sqlalchemy \
    opentelemetry-exporter-otlp \
    opentelemetry-sdk \
    sentry-sdk \
    arq \
    asyncpg \
    greenlet

COPY backend backend
COPY frontend frontend
COPY src src
COPY alembic.ini alembic.ini

CMD ["python", "-m", "shop_bot"]