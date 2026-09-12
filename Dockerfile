FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements ./requirements

RUN python -m pip install --no-cache-dir --require-hashes -r requirements/build.lock \
    && python -m pip install --no-cache-dir --require-hashes -r requirements/runtime.lock

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation . \
    && python -m pip check

CMD ["python", "-m", "shop_bot"]
