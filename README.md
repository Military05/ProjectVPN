# vless-shopbot

Production-oriented modular monolith for selling VLESS VPN subscriptions through Telegram. The project contains four runtime components: FastAPI backend, aiogram bot, ARQ worker and node agent.

## Architecture

The internal architecture is a Rich Domain Model built on Clean Architecture principles:

```text
apps (FastAPI / Telegram / Worker / Node Agent)
        ↓
application (use cases, queries, ports)
        ↓
domain (entities, value objects, policies, repository interfaces)
        ↑
infrastructure (SQLAlchemy, PostgreSQL, Redis, payments, panels, node HTTP)
```

Dependency rules:

- `domain` has no dependencies on FastAPI, aiogram, SQLAlchemy, Redis or external APIs;
- `application` depends on domain objects and interfaces only;
- `infrastructure` implements repository and gateway interfaces;
- `apps` are thin controllers and runtime composition roots;
- external dependencies are injected by `bootstrap/container.py`.

### Domain model

The core business objects are explicit classes with lifecycle rules:

- `User`, `UserContact`;
- `Tariff`, `Money`;
- `Subscription`, `SubscriptionPeriod`;
- `PaymentOrder`, `PaymentAttempt`, `PaymentEvent`, `PaymentTransaction`;
- `VpnConfiguration`;
- `Node`, `NodeTask`.

Examples of behavior owned by entities: payment state transitions, subscription activation/extension/expiration, VPN activation/revocation and node-task retry/failure handling.

### Project structure

```text
src/shop_bot/
├── apps/                         # runtime entrypoints and controllers
│   ├── api/
│   ├── bot/
│   ├── worker/
│   └── node_agent/
├── application/
│   ├── use_cases/                # executable business scenarios
│   ├── queries/                  # read services
│   ├── ports/                    # gateway and UoW abstractions
│   └── commands/                 # compatibility controllers
├── domain/
│   ├── entities/
│   ├── value_objects/
│   ├── services/
│   └── repositories/             # Protocol interfaces
├── infrastructure/
│   ├── persistence/
│   │   ├── sqlalchemy/           # tables, mappers, engine, UoW
│   │   └── repositories/         # SQLAlchemy implementations
│   ├── payments/
│   ├── nodes/
│   ├── panel/
│   ├── redis/
│   └── messaging/
└── bootstrap/                    # dependency injection and startup wiring
```

The old `infrastructure/db` import paths remain as thin compatibility proxies. Database tables, Alembic revisions, HTTP URLs, request/response schemas, environment variable names and payment-provider contracts are preserved.

## Main business flows

### Payment and subscription

1. Bot/API registers or resolves the user.
2. `CreatePayment` creates an idempotent `PaymentOrder` and provider attempt.
3. Webhook is normalized and stored by `IngestWebhook`.
4. Worker runs `ProcessPayment`.
5. `PaymentOrder.mark_paid()` validates the transition.
6. `ActivateSubscription` applies `SubscriptionPolicy` and creates/extends a paid period.
7. Provisioning is queued through the injected job queue.

### VPN provisioning

1. `ProvisionVpn` validates active subscription access.
2. `VpnProvisioningService` creates the domain configuration.
3. For a node-linked endpoint, a `NodeTask` is stored and dispatched.
4. For a legacy endpoint, a durable `PanelProvisionTask` is stored before ARQ dispatch; the remote panel call happens only in its leased dispatcher.
5. Successful remote provisioning is fenced before `VpnConfiguration.activate()`; local-finalization failures trigger compensating revoke.

### Node task execution

1. `DispatchNodeTask` locks the task and starts an attempt.
2. The node gateway sends a signed request to the node agent.
3. The domain task accepts success, schedules a bounded retry or fails.
4. VPN state and node health are updated transactionally.

## Compatibility guarantees

The refactor intentionally does not change:

- PostgreSQL table names or relationships;
- Alembic revision history;
- FastAPI and Node Agent route paths;
- Pydantic request/response models;
- Telegram commands and callback data;
- payment adapter interfaces and webhook flow;
- ARQ semantic job names (centralized in `JobName`);
- `SERVICE_MODE` values;
- environment variable names.

## Runtime components

```text
SERVICE_MODE=api
SERVICE_MODE=bot
SERVICE_MODE=worker
SERVICE_MODE=node_agent
```

Installed console scripts:

```text
shopbot-api
shopbot-bot
shopbot-worker
shopbot-node-agent
shopbot-seed
```

### Queue durability and recovery

Redis/ARQ is intentionally disposable and is used only for delivery, wake-ups and cache data. PostgreSQL stores the durable payment, node and panel work intents. On startup, the worker first verifies that the schema is at the Alembic head and then performs one bounded recovery pass; minute-based recovery jobs continue repairing lost queue deliveries while the worker runs.

## Local run with Docker Compose

```bash
cp .env.example .env
# Fill BOT_TOKEN, INTERNAL_API_KEY, ADMIN_API_TOKEN and payment/node secrets.
docker compose up --build
```

Endpoints:

- API docs: `http://localhost:8080/docs`
- health: `http://localhost:8080/health/live`
- admin UI: `http://localhost:8080/admin-ui`
- node agent: `http://localhost:8090`
- Prometheus: `http://localhost:9090`

## Manual run

Requires Python 3.12+, PostgreSQL and Redis.

```bash
python -m pip install -e '.[dev]'
alembic upgrade head
shopbot-node-agent
shopbot-api
shopbot-worker
shopbot-bot
```

## Tests and checks

```bash
pytest -q
python -m compileall -q src tests
pip install --no-build-isolation --no-deps -e .
```

The automated test suite covers type-annotation enforcement, domain state transitions, payment idempotency, paid subscription activation and refunds, persistence contracts, dependency boundaries, circular imports, API routes, webhook hardening, Node Agent routes, VLESS generation, node authentication, XUI integration boundaries and node runtime behavior.

## Required production dependency reproducibility check

The functional and security fixes in this archive are implemented, but the final reproducible dependency-lock step from **P20** is intentionally not completed in this build. Before treating an image as fully reproducible for production, generate and commit a Python 3.12 `requirements.lock` containing exact transitive versions and SHA256 hashes, then validate installation and the clean container build from that lock.

Recommended mandatory validation in a clean Python 3.12 environment:

```bash
python -m pip install 'pip-tools==7.5.1'
pip-compile --generate-hashes --resolver=backtracking --output-file=requirements.lock pyproject.toml
python -m pip install --require-hashes -r requirements.lock
pytest -q
python -m compileall -q src tests
docker compose build --no-cache
```

The dependency-lock check is successful only when the lock file is generated from the intended Python 3.12 environment, every dependency hash verifies during installation, the full test suite passes, and a clean Docker/Compose build completes without resolving unpinned Python packages from the live package index. Do not report dependency reproducibility as verified until those checks have actually been executed.

Detailed architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
