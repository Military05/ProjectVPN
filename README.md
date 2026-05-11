# vless-shopbot

Reworked `evansvl/vless-shopbot` as a modular monolith with a central backend and an optional multinode control plane.

## Services

- FastAPI backend API
- aiogram Telegram bot
- arq worker
- PostgreSQL 15+
- Redis
- FastAPI node-agent

## Layers

- `apps`: API, bot, worker, node-agent entrypoints
- `application`: commands, queries, orchestration
- `domain`: subscription, payment, VPN business rules
- `infrastructure`: SQLAlchemy Core repositories, Redis, payment adapters, panel adapter, node client/auth

The backend API remains the only place where domain writes happen. The bot remains a thin UX client that talks to the backend over HTTP.

## What was added for multinode mode

- `nodes`, `node_credentials`, `node_status`, `node_tasks`, `node_task_attempts`
- HMAC signed central -> node-agent requests with timestamp, nonce and idempotency key
- `apps/node_agent` service with:
  - `GET /agent/health`
  - `GET /agent/capabilities`
  - `GET /agent/status`
  - `POST /agent/clients/provision`
  - `POST /agent/clients/revoke`
- worker orchestration for node task dispatch and status sync
- `server_endpoints.node_id` and `server_endpoints.local_inbound_id` to map public VLESS endpoints to managed nodes and local inbounds
- backward-compatible legacy fallback through the existing global `panel_adapter` when an endpoint is not linked to any node

## Provisioning flow

Central path:

1. payment webhook/event is ingested into central DB
2. payment event processing activates subscription period only after a valid paid transition
3. provisioning command selects an enabled endpoint
4. if the endpoint is linked to a node, central creates `vpn_configurations` in `provisioning`, creates a `node_task`, and worker dispatches it to node-agent
5. node-agent performs local provision via stub or XUI runtime
6. central activates the VPN config and still builds the final VLESS URI from central DB

Legacy path:

- if `server_endpoints.node_id` is `NULL`, provisioning still falls back to the existing single-panel adapter

## Local run with Docker Compose

1. Copy the environment template.

```bash
cp .env.example .env
```

2. Fill the required values in `.env`.

At minimum:

- `BOT_TOKEN`
- `INTERNAL_API_KEY`
- `ADMIN_API_TOKEN`
- `DEMO_NODE_SHARED_SECRET`

3. Start the stack.

```bash
docker compose up --build
```

4. Open:

- API docs: `http://localhost:8080/docs`
- Node agent: `http://localhost:8090`
- Prometheus: `http://localhost:9090`
- Health: `http://localhost:8080/health/live`
- Admin UI: `http://localhost:8080/admin-ui`

Compose runs a demo node-agent and auto-registers a demo node for the seeded endpoint.

## Admin web panel

The API now serves a lightweight built-in admin panel at `/admin-ui`. It uses the existing admin API endpoints and the existing `ADMIN_API_TOKEN` contract; the token is sent as `X-Admin-Token` and stored only in browser session storage. No backend route, payload, or environment variable names were changed for the panel.

The panel includes dashboard metrics, clients derived from existing subscriptions/payments/VPN configs, servers, endpoints, nodes, tariffs, payments, subscriptions, VPN configurations, node tasks, system health, empty/loading/error states, and guarded destructive actions.


## Service modes

The same codebase can run as:

- `SERVICE_MODE=api`
- `SERVICE_MODE=bot`
- `SERVICE_MODE=worker`
- `SERVICE_MODE=node_agent`

## Node admin endpoints

Use header `X-Admin-Token: <ADMIN_API_TOKEN>`.

- `GET /admin/nodes`
- `POST /admin/nodes`
- `POST /admin/nodes/{node_id}/sync`
- `GET /admin/nodes/tasks`
- `POST /admin/nodes/tasks/{node_task_id}/dispatch`

## Existing admin endpoints

- `GET /admin/tariffs`
- `POST /admin/tariffs`
- `GET /admin/servers`
- `POST /admin/servers`
- `POST /admin/server-endpoints`
- `GET /admin/subscriptions`
- `GET /admin/vpn-configurations`
- `GET /admin/payment-orders`

## New environment variables

Central node orchestration:

- `NODE_REQUEST_TIMEOUT_SECONDS`
- `NODE_HTTP_VERIFY_TLS`
- `NODE_TIMESTAMP_TOLERANCE_SECONDS`
- `NODE_TASK_MAX_ATTEMPTS`
- `NODE_TASK_RETRY_BASE_SECONDS`

Node-agent runtime:

- `NODE_AGENT_NODE_KEY`
- `NODE_AGENT_KEY_ID`
- `NODE_AGENT_SHARED_SECRET`
- `NODE_AGENT_RUNTIME_MODE=stub|xui`
- `NODE_AGENT_INBOUND_ID`
- `NODE_AGENT_PUBLIC_HOST`
- `NODE_AGENT_PUBLIC_PORT`

Demo bootstrap:

- `DEMO_NODE_AUTO_REGISTER`
- `DEMO_NODE_KEY`
- `DEMO_NODE_API_BASE_URL`
- `DEMO_NODE_KEY_ID`
- `DEMO_NODE_SHARED_SECRET`

## Manual run without Docker

```bash
python -m pip install -e .[dev]
alembic upgrade head
shopbot-node-agent
shopbot-api
shopbot-worker
shopbot-bot
```

## Tests

```bash
pytest -q
python -m compileall src tests
```

## Notes

- the VLESS URI builder still uses central DB data from `servers`, `server_endpoints`, `vpn_configurations`
- `servers.host` remains the public VLESS host and is not treated as the node API endpoint
- production should use HTTPS for node-agent and can later be hardened with mTLS and secret rotation tooling
