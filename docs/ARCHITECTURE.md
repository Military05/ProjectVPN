# Architecture

## 1. Architectural style

`vless-shopbot` is a modular monolith with multiple runtime processes and one shared codebase. The architecture combines:

- Rich Domain Model;
- Clean Architecture dependency direction;
- Repository Pattern;
- Unit of Work;
- Service Layer and explicit Use Cases;
- dependency injection;
- adapters for payments, Redis/ARQ, panel APIs and node APIs.

The goal is not to split the system into microservices. API, bot, worker and node agent remain independently runnable components of one product.

## 2. Dependency direction

```text
apps ───────────────► application ───────────────► domain
  │                         ▲                         ▲
  └──── bootstrap ──────────┴──── infrastructure ────┘
```

Rules:

1. Domain code imports only Python standard library and other domain modules.
2. Application use cases use domain entities, policies and ports.
3. Infrastructure implements the ports and maps persistence rows to entities.
4. Apps translate HTTP/Telegram/worker input into use-case calls.
5. Bootstrap creates concrete adapters and injects them.

Automated tests enforce the first three rules and check the internal import graph for cycles.

## 3. Layers

### Domain

`domain/entities` contains state and business behavior. Entities do not know about SQLAlchemy rows, HTTP requests, Telegram messages or Redis jobs.

`domain/value_objects` contains immutable validated values such as `Money` and typed identifiers.

`domain/services` contains rules that involve several entities but no infrastructure. `SubscriptionPolicy` calculates paid access periods. `VpnProvisioningService` prepares a VPN configuration from a selected endpoint.

`domain/repositories/interfaces.py` defines Protocol-based persistence contracts used by application services.

### Application

`application/use_cases` contains one class per business scenario:

- user registration;
- payment creation and webhook ingestion;
- payment processing;
- subscription activation and reconciliation;
- VPN provisioning and revocation;
- node-task dispatch and node status synchronization;
- outbox publishing;
- admin operations.

Use cases coordinate transactions and domain objects. They do not import FastAPI, aiogram, SQLAlchemy or concrete payment/node implementations.

`application/queries/services.py` contains read-only application services for Telegram/API dashboards and node lists.

`application/commands` and the old query functions are compatibility controllers. They delegate immediately to injected use-case/query objects and contain no business logic.

### Infrastructure

`infrastructure/persistence/sqlalchemy` owns metadata, tables, mappers, engine setup and Unit of Work.

`infrastructure/persistence/repositories` implements domain repository protocols with SQLAlchemy Core and async connections.

`infrastructure/payments` contains provider adapters. The registry is injected through the `PaymentGatewayRegistry` port. CryptoBot and Heleket remain available for non-production testing but are quarantined from the production registry pending full provider-contract rewrites.

`infrastructure/nodes` contains signed HTTP communication with node agents.

`infrastructure/messaging` implements the application job queue port using ARQ.

`infrastructure/db` is intentionally retained as a compatibility import facade. New code uses `infrastructure/persistence`.

### Apps

FastAPI routes validate transport data and call application services. They do not open database transactions directly.

The Telegram router is built by `TelegramBotController`; message rendering is isolated in `BotMessagePresenter`.

Worker functions are transport adapters for ARQ job names and delegate to use cases. Dynamic job names come from the application-level `JobName` enum and are explicitly registered under those semantic names.

Node Agent routes validate signed requests and call a runtime adapter.

## 4. Persistence mapping

```text
Domain Entity
    ↓ / ↑ mapper
SQLAlchemy RowMapping
    ↓
Repository implementation
    ↓
SqlAlchemyUnitOfWork
    ↓
PostgreSQL transaction
```

ORM/persistence records are not used as domain objects. Existing migration revisions are immutable; schema evolution is additive through new Alembic revisions.

## 5. Transaction boundaries

Each use case obtains a Unit of Work through an injected factory. `SqlAlchemyUnitOfWork` opens one connection and transaction, constructs repositories, commits on success and rolls back on error.

External calls are made outside database transactions. Payment invoice creation is protected by a durable leased `PaymentAttempt.CREATED` claim, node requests use durable `NodeTask` leases, and legacy panel provisioning uses durable `PanelProvisionTask` leases with fenced finalization and compensation.

Redis/ARQ is a disposable delivery and wake-up transport, not a source of business truth. Redis persistence is intentionally disabled; payment events and node/panel tasks remain durable in PostgreSQL. After verifying that the database schema is at the Alembic head, each worker runs one bounded startup recovery pass for received payment events and due or stale node/panel tasks. The one-minute recovery jobs then provide the regular repair loop.

## 6. Compatibility boundaries

The following are treated as external contracts:

- API paths, methods and schemas;
- Node Agent paths and signing format;
- Telegram commands, button texts and callback data;
- ARQ job names and arguments;
- payment provider adapter behavior;
- environment variable names;
- database schema and Alembic history.

Compatibility tests and static route snapshots protect these boundaries.

## 7. Extension points

A new payment provider implements `PaymentGateway` and is registered in the payment registry.

A new queue backend implements `JobQueue`.

A new panel or node implementation satisfies `PanelGateway` or `NodeGateway`.

A different persistence technology implements repository protocols and Unit of Work without changing domain entities or use cases.
