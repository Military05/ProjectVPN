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
- Завершён `CHANGE-04`: физическая Node VPN operation однозначно определяется кортежем `(vpn_configuration_id, operation, vpn_generation)`, а panel revoke — `(vpn_configuration_id, vpn_generation)`; точные unique indexes уже находятся в expand-миграции `0007` и SQLAlchemy metadata.
- Node и panel repositories используют DB-authoritative `INSERT ... ON CONFLICT DO NOTHING RETURNING`: если параллельная запись выиграла с другим UUID/idempotency key, проигравший writer возвращает уже сохранённого владельца физической операции и не создаёт вторую identity.
- Lookup Node VPN task стал status-agnostic и возвращает также `SUCCEEDED/FAILED/CANCELLED`. `ProvisionVpn` повторно ставит в очередь только `PENDING`, не дублирует `IN_PROGRESS`, локально финализирует `SUCCEEDED` без удалённого replay и направляет `FAILED/CANCELLED` в cleanup.
- Конфигурация `FAILED` больше не допускает выбор другого endpoint или создание replacement: ставится durable `replacement_cleanup` через `RevokeVpn`, cleanup выполняется с прежними client UUID/inbound/endpoint, и новая конфигурация разрешается только после состояния `REVOKED`.
- Переходы `cancel_provisioning_for_revoke()` и `request_cleanup_from_failed()` теперь атомарно переводят конфигурацию в `REVOKING/REVOKED desired` с новой generation. Успешная panel-компенсация завершает этот переход в `REVOKED`, записывает audit-событие и не оставляет конфигурацию зависшей.
- Завершён `CHANGE-05`: `SyncExpiredSubscriptions` заменён на `ReconcileSubscriptions`; старое имя сохранено только как command/worker compatibility wrapper для ранее поставленных внутренних задач, а не как активный cron-путь.
- Reconciliation защищён PostgreSQL maintenance lease: atomic `INSERT ... ON CONFLICT ... WHERE lease_expires_at <= now RETURNING`, fenced renew/release по `(lease_name, owner_token)`, точный ответ `{"status":"already_running"}` конкурентному запуску и возобновление после истечения lease упавшего владельца.
- Вместо трёх неограниченных full scan запросов используется один keyset/`LIMIT` `UNION ALL` только по шести классам аномалий: истечение подписки, revoke без entitlement, cleanup `FAILED`, отсутствующая VPN, repair provisioning и repair revoke. Старые full-scan repository methods удалены.
- Размер запуска ограничен `RECONCILIATION_BATCH_SIZE × RECONCILIATION_MAX_BATCHES_PER_RUN`, lease продлевается после каждого обработанного batch. `PENDING/IN_PROGRESS` physical tasks исключены из repair-выборки и остаются под управлением собственных recovery loops; `SUCCEEDED` task с устаревшим локальным состоянием финализируется локально без нового удалённого side effect.
- `BACKGROUND_SYNC_INTERVAL_SECONDS` теперь действительно задаёт cadence reconciliation cron; значения валидируются как точно представимые ARQ cron-интервалы. Значения batch/max-batches/lease подключены из `Settings` и перечислены в `.env.example`.
- Завершён `CHANGE-06`: активный fake outbox удалён. Все lifecycle/admin facts записываются через отдельный `AuditRepository.append()` в `audit_events` внутри той же PostgreSQL UoW-транзакции, что и породившее их бизнес-изменение; `PaymentRepository` больше не содержит outbox API.
- Audit log стал действительно immutable: application repository имеет только append-операцию, а миграция `20260913_0010` добавляет PostgreSQL `BEFORE UPDATE OR DELETE` trigger с ошибкой `55000`. Индексы журнала приведены к `(aggregate_type, aggregate_id, created_at DESC)` и `(event_name, created_at DESC)`.
- Rolling compatibility сохранена: `outbox_events` пока не удаляется; `AFTER INSERT` trigger зеркалирует записи старых replica в audit log по nullable unique `legacy_outbox_event_id`, а catch-up copy запускается после установки trigger и идемпотентен через `ON CONFLICT DO NOTHING`.
- `PublishOutbox`, outbox list/mark/reschedule state machine, `JobName.PUBLISH_OUTBOX`, producers и cron удалены. Старое ARQ-имя `publish_outbox` зарегистрировано только как database-free tombstone и всегда возвращает `{"status":"deprecated_noop"}` для уже лежащих в Redis задач.
- Добавлен ADR `docs/adr/0001-transactional-audit-log.md`: прежний publisher только писал log и помечал строку доставленной, хотя broker/consumer/ack отсутствовали; поэтому системе нужен audit log, а не фиктивная модель доставки.
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
- Целевой набор `CHANGE-06`: `115 passed` — append-only repository, transactional rollback audit-факта, audit wiring всех затронутых use cases, отсутствие активной outbox state machine, rolling mirror/catch-up/immutability migration contract и database-free Redis tombstone.
- Целевой набор `CHANGE-05`: `51 passed` — maintenance lease/owner fencing, точный concurrent result, bounded keyset batches, шесть anomaly actions, enqueue failure, lease loss, отсутствие full-scan cron, compatibility wrapper, repository SQL contract и локальное завершение `SUCCEEDED` Node/panel task без replay.
- Целевой набор `CHANGE-04`: `70 passed` — domain transitions, Node/panel task states, terminal local recovery, cleanup-before-replacement, original endpoint/client preservation, physical-index/repository conflict contracts, same-key central retry и Node Agent ambiguous-operation recovery.
- Целевые regression-тесты `CHANGE-12`: `10 passed`; совместно с тестами `CHANGE-11`: `28 passed`.
- PostgreSQL-набор `CHANGE-12` корректно собирается, но в текущей среде дал `6 skipped`, поскольку здесь отсутствуют Docker и PostgreSQL. До запуска этих шести тестов на настоящем PostgreSQL пункт считается реализованным, но не полностью подтверждённым.
- Целевые regression-тесты `CHANGE-11`: `18 passed` (HTTP defaults/bounds/body, API query contract, probe row/headers, application forwarding, repository window/stable ordering и UI contract).
- Regression-тесты `CHANGE-08`: `5 passed` (точные pins/hashes всех lock-файлов, runtime ⊂ dev, build metadata, Dockerfile contract и живой FastAPI/Prometheus HTTP smoke).
- `node --check` для admin UI JavaScript — успешно; `pip check` — зависимости согласованы.
- Чистая установка `build.lock` + `runtime.lock` с `--require-hashes`, установка проекта без dependency resolution и `pip check` — успешно; `GET /health/live` вернул `200`, `_IncludedRouter` в маршрутах отсутствует.
- Чистая установка `dev.lock` с `--require-hashes` и целевой pytest-набор — успешно.
- Полный `pytest -q` после `CHANGE-04`: `222 passed, 30 failed, 6 skipped`. От baseline `198 passed, 34 failed, 6 skipped` добавлено 20 новых успешных CHANGE-04 сценариев и восстановлены четыре относящиеся к operation identity проверки; новых падений нет, оставшиеся 30 унаследованы.
- Полный `pytest -q` после `CHANGE-05`: `251 passed, 30 failed, 6 skipped`. Добавлено 29 успешных CHANGE-05 сценариев; перечень 30 унаследованных падений не изменился, новых падений нет.
- Полный `pytest -q` после `CHANGE-06`: `268 passed, 19 failed, 6 skipped`. Добавлено 6 новых проверок и восстановлено 11 старых тестов, чьи fake repositories/metadata expectations всё ещё описывали прежний outbox; новых падений нет. Оставшиеся 19 унаследованных падений относятся к route snapshots и XUI/production-settings contract, а не к `CHANGE-06`.
- `pip check`, `git diff --check`, `alembic heads` (`20260913_0010`) и `compileall` после `CHANGE-06` — успешно.
- В текущей среде нет Docker CLI, поэтому реальный `docker compose build --no-cache` здесь не запускался; Dockerfile contract проверен автоматическим тестом, но clean image acceptance нужно выполнить на ноутбуке.
- `docker-compose.yml` разбирается YAML-парсером; присутствуют `migrate`, healthcheck API и локальный bind Prometheus.

## Осталось для следующего этапа

- Запустить 6 тестов из `tests/integration/test_admin_creation_postgresql.py` на настоящем PostgreSQL 15+ по `CHANGE12_CHECKLIST_RU.md`. Ожидаемый итог — `6 passed`, без `skipped`; это последний acceptance-шаг для полного подтверждения `CHANGE-12`.
- Завершить `CHANGE-07`: central journal retirement use case поверх уже существующих schema fields, partial index и authenticated Node Agent endpoint.
- Завершить locking/capacity reservation в каждом production writer и panel equivalent.
- Выполнить `docker compose build --no-cache` по уже зафиксированным lock-файлам и ручной smoke административной панели на машине с Docker.
- Выполнить integration tests на PostgreSQL/Redis и проверить upgrade path 0006→0007→0008→0009.
- Проверить на настоящем PostgreSQL rolling upgrade до `20260913_0010`: backfill старых строк, mirror INSERT старой replica, запрет UPDATE/DELETE audit rows и сохранение `outbox_events` до будущей contract-миграции.
- Исправить 19 унаследованных падений полного набора тестов перед production acceptance gate.

Текущая ветка `main` является промежуточным checkpoint для продолжения работы, а не заявлением о полном прохождении production acceptance gate.

## Что делать в следующем промпте

Следующий отдельный этап разработки — выполнить только `CHANGE-07`: завершить central journal retirement use case поверх уже существующих schema fields, partial index и authenticated Node Agent endpoint. Не повторять завершённые пункты, включая `CHANGE-04`, `CHANGE-05` и `CHANGE-06`.

После следующей правки снова выполнить целевые тесты, `compileall` и полный `pytest`, сравнив результат с текущим baseline `268 passed, 19 failed, 6 skipped`. Отдельно на ноутбуке всё ещё нужно выполнить `docker compose build --no-cache`, шесть PostgreSQL-сценариев `CHANGE-12` по `CHANGE12_CHECKLIST_RU.md` и реальный rolling-upgrade smoke миграции `20260913_0010`.
