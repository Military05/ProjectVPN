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
- Реализация `CHANGE-12` доведена на уровне кода: создание tariff/server/node/endpoint использует DB-authoritative `INSERT ... ON CONFLICT DO NOTHING RETURNING`; для node и endpoint заданы точные conflict targets, а server намеренно учитывает оба независимых уникальных ключа — имя и host.
- Пустой `RETURNING` преобразуется в domain `ConflictError` и HTTP 409; проигравший node INSERT не доходит до создания credential. Ошибки дочерней записи остаются внутри общей UoW-транзакции и откатывают родителя; `CHECK`/`FOREIGN KEY` не перехватываются как conflict.
- Добавлены 10 быстрых regression-тестов SQL/API/use-case контракта и 6 настоящих PostgreSQL integration-сценариев: параллельные tariff/server/node/endpoint запросы, один credential победителя, `NULLS NOT DISTINCT`, rollback и немаскированные ограничения.
- Завершён `CHANGE-08` на уровне кода и чистой Python-установки: добавлены SHA-256 hash-pinned `requirements/runtime.lock`, `requirements/dev.lock` и `requirements/build.lock` для Python 3.12; dev-lock является точным надмножеством runtime-lock, а build-lock совпадает с `build-system.requires`.
- Runtime зафиксирован на совместимой паре FastAPI `0.136.3` и `prometheus-fastapi-instrumentator` `7.1.0`; диапазон FastAPI ограничен `<0.137.0`, потому что ветка `0.137+` вводит lazy `_IncludedRouter`, который instrumentator `7.1.0` не умеет обрабатывать.
- Dockerfile устанавливает только `build.lock` и `runtime.lock` с `--require-hashes`, затем проект с `--no-deps --no-build-isolation` и выполняет `pip check`; обновление pip и разрешение project dependency ranges во время image build удалены, digest Python 3.12 base image сохранён.
- Добавлен пользовательский чек-лист `CHANGE12_CHECKLIST_RU.md` с изолированным PostgreSQL 16 стендом, командами тестов и ручной проверкой административной панели.
- Redis оставлен disposable; Prometheus ограничен bind `127.0.0.1:9090`; добавлен отдельный Compose migrate job и schema assertion для worker/readiness.
- Добавлен authenticated Node Agent journal retirement endpoint.
- После schema assertion worker выполняет один bounded startup recovery pass (до 100 записей каждого типа): received payment events, stale/due node tasks, panel provision tasks и panel revoke tasks.
- Удалён глобальный Redis service locator (`get_redis`/`set_redis`): Redis теперь принадлежит DI container; добавлены architecture guards против возврата locator и новых production imports через compatibility `infrastructure/db`.
- Завершён `CHANGE-10`: создание и повтор заказа вынесены в stateless `OrderFlow`; удалены process-local `_order_intents` и `OrderIntentContext`; после перезапуска бота callback повторно получает актуальный enabled-тариф из backend и использует прежний idempotency key `tg-ui2:{telegram_id}:{tariff_id}:{intent_id}`.
- `Settings.bot_dedup_ttl_seconds` теперь передаётся в Telegram controller и используется для всех Telegram dedup keys вместо hardcoded TTL; неположительное значение отклоняется конфигурацией.

## Проверено

- `python -m compileall -q src tests alembic` — успешно.
- Целевые regression-тесты `CHANGE-12`: `10 passed`; совместно с тестами `CHANGE-11`: `28 passed`.
- PostgreSQL-набор `CHANGE-12` корректно собирается, но в текущей среде дал `6 skipped`, поскольку здесь отсутствуют Docker и PostgreSQL. До запуска этих шести тестов на настоящем PostgreSQL пункт считается реализованным, но не полностью подтверждённым.
- Целевые regression-тесты `CHANGE-11`: `18 passed` (HTTP defaults/bounds/body, API query contract, probe row/headers, application forwarding, repository window/stable ordering и UI contract).
- Regression-тесты `CHANGE-08`: `5 passed` (точные pins/hashes всех lock-файлов, runtime ⊂ dev, build metadata, Dockerfile contract и живой FastAPI/Prometheus HTTP smoke).
- `node --check` для admin UI JavaScript — успешно; `pip check` — зависимости согласованы.
- Чистая установка `build.lock` + `runtime.lock` с `--require-hashes`, установка проекта без dependency resolution и `pip check` — успешно; `GET /health/live` вернул `200`, `_IncludedRouter` в маршрутах отсутствует.
- Чистая установка `dev.lock` с `--require-hashes` и целевой pytest-набор — успешно.
- Полный `pytest -q` после `CHANGE-08`: `198 passed, 34 failed, 6 skipped`. По сравнению с baseline после `CHANGE-12` добавились пять новых успешных тестов; точный набор из 34 унаследованных падений не изменился.
- В текущей среде нет Docker CLI, поэтому реальный `docker compose build --no-cache` здесь не запускался; Dockerfile contract проверен автоматическим тестом, но clean image acceptance нужно выполнить на ноутбуке.
- `docker-compose.yml` разбирается YAML-парсером; присутствуют `migrate`, healthcheck API и локальный bind Prometheus.

## Осталось для следующего этапа

- Запустить 6 тестов из `tests/integration/test_admin_creation_postgresql.py` на настоящем PostgreSQL 15+ по `CHANGE12_CHECKLIST_RU.md`. Ожидаемый итог — `6 passed`, без `skipped`; это последний acceptance-шаг для полного подтверждения `CHANGE-12`.
- Полностью довести cleanup-before-replacement для FAILED VPN operations и central journal retirement use case.
- Завершить surgical reconciliation с maintenance lease и bounded anomaly batches.
- Переключить active fake outbox code на audit log, сохранив compatibility tombstone.
- Завершить locking/capacity reservation в каждом production writer и panel equivalent.
- Выполнить `docker compose build --no-cache` по уже зафиксированным lock-файлам и ручной smoke административной панели на машине с Docker.
- Выполнить integration tests на PostgreSQL/Redis и проверить upgrade path 0006→0007→0008→0009.
- Исправить 34 унаследованных падения полного набора тестов перед production acceptance gate.

Текущая ветка `main` является промежуточным checkpoint для продолжения работы, а не заявлением о полном прохождении production acceptance gate.

## Что делать в следующем промпте

Сначала выполнить на ноутбуке `docker compose build --no-cache`, поднять стенд и проверить `GET /health/live` и `/admin-ui`; затем запустить раздел «Вариант 1 — строгая автоматическая проверка» из `CHANGE12_CHECKLIST_RU.md`. Ожидается отсутствие `_IncludedRouter`, `10 passed` для unit и `6 passed` для PostgreSQL integration без `skipped`.

Следующий отдельный этап разработки — выполнить только `CHANGE-04`: полностью довести physical VPN operation identity и cleanup-before-replacement state machine до начала нового reconciliation. Не повторять `CHANGE-08`, `CHANGE-10`, `CHANGE-11`, `CHANGE-12`, `CHANGE-13`, `CHANGE-14`, `CHANGE-15` и уже выполненную targeted-часть `CHANGE-16`.

После следующей правки снова выполнить целевые тесты, `compileall` и полный `pytest`, сравнив результат с текущим baseline `198 passed, 34 failed, 6 skipped` (шесть skipped должны исчезнуть в среде с PostgreSQL).
