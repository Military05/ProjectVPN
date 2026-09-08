# Отчёт о промежуточной реализации

В архив внесена безопасная частичная реализация спецификации `Решения проблем`.

## Готово

- XUI inbound contract: при `NODE_AGENT_RUNTIME_MODE=xui` значение `NODE_AGENT_INBOUND_ID` проверяется как положительное целое; добавлено свойство `node_agent_xui_inbound_id`.
- `XuiNodeRuntime` больше не наследуется от stub runtime; runtimes реализуют общий Protocol независимо.
- Добавлен authenticated `GET /agent/snapshot`; клиент использует fallback только при 404/405 и выполняет legacy health/capabilities/status параллельно с ограничением в три запроса.
- Некорректный XUI `inbound_id` отклоняется до вызова XUI.
- Добавлены поля health/probe lease/capacity в SQLAlchemy metadata и expand migrations 0007/0008, contract migration 0009.
- Добавлены immutable `audit_events`, `maintenance_leases`, `AuditRepository` и UoW wiring.
- Добавлена базовая node selection policy с freshness, health, capacity и weighted random selection.
- Health sync переведён на bounded worker pool, per-node completion timestamp и probe token fencing; успешные task operations больше не меняют node health.
- Payment event lookup переведён на `(provider, event_key)` с rolling compatibility fallback; `PaymentAttempt.apply_provider_status()` стал monotonic и возвращает `APPLIED/DUPLICATE/STALE`; ProcessPayment перечитывает attempt после блокировки order.
- Добавлены server pagination headers/parameters для admin subscriptions, VPN configurations и payment orders.
- Создание node/server/endpoint/tariff использует DB arbitration через `ON CONFLICT DO NOTHING` и возвращает domain conflict вместо гонки pre-check → insert.
- Redis оставлен disposable; Prometheus ограничен bind `127.0.0.1:9090`; добавлен отдельный Compose migrate job и schema assertion для worker/readiness.
- Добавлен authenticated Node Agent journal retirement endpoint.
- После schema assertion worker выполняет один bounded startup recovery pass (до 100 записей каждого типа): received payment events, stale/due node tasks, panel provision tasks и panel revoke tasks.
- Удалён глобальный Redis service locator (`get_redis`/`set_redis`): Redis теперь принадлежит DI container; добавлены architecture guards против возврата locator и новых production imports через compatibility `infrastructure/db`.

## Проверено

- `python -m compileall -q src tests alembic` — успешно.
- Целевые тесты новых изменений: `10 passed`.
- Полный `pytest -q` впервые выполнен: `159 passed, 34 failed`. Все 34 падения воспроизводятся на входном checkpoint и относятся к ранее частично реализованным config/payment/node/runtime/contract changes; выбранные в этом этапе изменения новых падений не добавили. Для сравнения нетронутая копия входного checkpoint: `154 passed, 35 failed` (четыре новых passing-теста плюс исправленная отсутствующая type annotation объясняют разницу).
- `docker-compose.yml` разбирается YAML-парсером; присутствуют `migrate`, healthcheck API и локальный bind Prometheus.

## Осталось для следующего этапа

- Полностью довести cleanup-before-replacement для FAILED VPN operations и central journal retirement use case.
- Завершить surgical reconciliation с maintenance lease и bounded anomaly batches.
- Переключить active fake outbox code на audit log, сохранив compatibility tombstone.
- Завершить locking/capacity reservation в каждом production writer и panel equivalent.
- Stateless Telegram retry/order_flow и полная admin pagination UI.
- Сгенерировать настоящие hash-pinned runtime/dev/build lock-файлы и выполнить clean install/pytest acceptance gate.
- Выполнить integration tests на PostgreSQL/Redis и проверить upgrade path 0006→0007→0008→0009.
- Исправить 34 унаследованных падения полного набора тестов перед production acceptance gate.

Архив является промежуточным checkpoint для продолжения работы, а не заявлением о полном прохождении production acceptance gate.
