# Отчёт о промежуточной реализации

В ветке `main` репозитория `ProjectVPN` ведётся безопасная поэтапная реализация спецификации `Решения проблем`.

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
- Завершён `CHANGE-11`: admin API для subscriptions, VPN configurations, payment orders, nodes и node tasks принимает `limit=1..200`/`offset>=0` с backward-compatible defaults `100/0`, запрашивает у repository `limit + 1`, возвращает исходный `list[...]` и заголовки `X-Page-Limit`, `X-Page-Offset`, `X-Has-More` без `COUNT(*)`.
- Все пять paginated repository-запросов имеют стабильную сортировку с primary-key tiebreaker; admin UI использует server-side страницы по 50 записей, переходы `offset ± 50`, состояние `has_more` из response headers и явно обозначает, что показана только текущая страница.
- Создание node/server/endpoint/tariff использует DB arbitration через `ON CONFLICT DO NOTHING` и возвращает domain conflict вместо гонки pre-check → insert.
- Redis оставлен disposable; Prometheus ограничен bind `127.0.0.1:9090`; добавлен отдельный Compose migrate job и schema assertion для worker/readiness.
- Добавлен authenticated Node Agent journal retirement endpoint.
- После schema assertion worker выполняет один bounded startup recovery pass (до 100 записей каждого типа): received payment events, stale/due node tasks, panel provision tasks и panel revoke tasks.
- Удалён глобальный Redis service locator (`get_redis`/`set_redis`): Redis теперь принадлежит DI container; добавлены architecture guards против возврата locator и новых production imports через compatibility `infrastructure/db`.
- Завершён `CHANGE-10`: создание и повтор заказа вынесены в stateless `OrderFlow`; удалены process-local `_order_intents` и `OrderIntentContext`; после перезапуска бота callback повторно получает актуальный enabled-тариф из backend и использует прежний idempotency key `tg-ui2:{telegram_id}:{tariff_id}:{intent_id}`.
- `Settings.bot_dedup_ttl_seconds` теперь передаётся в Telegram controller и используется для всех Telegram dedup keys вместо hardcoded TTL; неположительное значение отклоняется конфигурацией.

## Проверено

- `python -m compileall -q src tests alembic` — успешно.
- Целевые regression-тесты `CHANGE-11`: `18 passed` (HTTP defaults/bounds/body, API query contract, probe row/headers, application forwarding, repository window/stable ordering и UI contract).
- `node --check` для admin UI JavaScript — успешно; `pip check` — зависимости согласованы.
- Полный `pytest -q`: `183 passed, 34 failed`. До `CHANGE-11` тот же `main` давал `165 passed, 34 failed`; все 18 новых regression-тестов проходят, точный набор из 34 унаследованных падений не изменился.
- `docker-compose.yml` разбирается YAML-парсером; присутствуют `migrate`, healthcheck API и локальный bind Prometheus.

## Осталось для следующего этапа

- Полностью довести cleanup-before-replacement для FAILED VPN operations и central journal retirement use case.
- Завершить surgical reconciliation с maintenance lease и bounded anomaly batches.
- Переключить active fake outbox code на audit log, сохранив compatibility tombstone.
- Завершить locking/capacity reservation в каждом production writer и panel equivalent.
- Сгенерировать настоящие hash-pinned runtime/dev/build lock-файлы и выполнить clean install/pytest acceptance gate.
- Выполнить integration tests на PostgreSQL/Redis и проверить upgrade path 0006→0007→0008→0009.
- Исправить 34 унаследованных падения полного набора тестов перед production acceptance gate.

Текущая ветка `main` является промежуточным checkpoint для продолжения работы, а не заявлением о полном прохождении production acceptance gate.

## Что делать в следующем промпте

Рекомендуемый следующий один пункт — полностью закрыть `CHANGE-12` (race-safe admin creation): проверить DB-authoritative создание tariff/server/node/endpoint и добавить конкурентный PostgreSQL-тест «один 201, один 409, ни одного 500», включая гарантию, что credential создаётся только для выигравшего node INSERT. Начинать с актуальной ветки `main`; `CHANGE-10`, `CHANGE-11`, `CHANGE-13` и выполненную targeted-часть `CHANGE-16` повторно не делать.

После следующей правки снова выполнить целевые тесты, `compileall` и полный `pytest`, сравнив результат с текущим baseline `183 passed, 34 failed`.
