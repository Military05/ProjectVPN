# Проверка CHANGE-12 — конкурентное создание объектов администратором

Этот файл нужен для проверки только `CHANGE-12`. Он не относится к уже завершённым
`CHANGE-10`, `CHANGE-11`, `CHANGE-13` и выполненной части `CHANGE-16`.

## Текущий статус checkpoint

- SQL-конфликты для tariff/server/node/endpoint реализованы через PostgreSQL
  `INSERT ... ON CONFLICT DO NOTHING RETURNING ...`.
- Пустой `RETURNING` преобразуется в domain `ConflictError`, а API-контракт — в
  HTTP `409`.
- Быстрые тесты CHANGE-12: `10 passed`.
- Полный набор в среде разработки: `193 passed, 34 failed, 6 skipped`.
- Те же 34 падения уже существовали до CHANGE-12; десять новых unit-тестов прошли.
- Шесть тестов, помеченных `postgresql`, здесь пропущены только из-за отсутствия
  Docker/PostgreSQL. Именно их нужно запустить на ноутбуке по инструкции ниже.

### Известный унаследованный блокер полного UI-теста

При свежем разрешении диапазонов из `pyproject.toml` в текущей среде установились
FastAPI `0.141.1` и `prometheus-fastapi-instrumentator` `7.1.0`. Эта комбинация
может завершить запрос до административного маршрута ошибкой
`AttributeError: '_IncludedRouter' object has no attribute 'path'`.

Это одна из старых проблем текущего checkpoint, а не регрессия CHANGE-12. Целевые
integration-тесты из этого файла используют настоящие административные маршруты,
UoW, PostgreSQL и production-обработчик `ConflictError`, но изолируют их от
не относящегося к CHANGE-12 middleware наблюдаемости.

Если такая ошибка появилась при ручном запуске панели:

1. сохраните полный вывод `docker compose ... logs api`;
2. всё равно выполните строгие PostgreSQL-тесты из варианта 1;
3. не отмечайте полный end-to-end UI acceptance завершённым;
4. следующим этапом выполните `CHANGE-08` — зафиксируйте проверенный dependency
   graph и устраните эту несовместимость воспроизводимым способом.

## Что должно быть гарантировано

При двух одновременных попытках создать одинаковый объект база данных должна сама
выбрать победителя:

- один запрос возвращает HTTP `201 Created`;
- второй запрос возвращает HTTP `409 Conflict`;
- HTTP `500` не возникает;
- в базе остаётся ровно одна запись;
- для узла создаётся ровно один credential — только для запроса-победителя;
- если создание дочерней записи завершилось ошибкой, родительская запись откатывается;
- нарушения `CHECK` и `FOREIGN KEY` не превращаются ошибочно в `409`.

Проверяются четыре административных операции:

| Объект | HTTP-маршрут | Конфликт определяет |
| --- | --- | --- |
| Тариф | `POST /admin/tariffs` | уникальное имя тарифа |
| Сервер | `POST /admin/servers` | уникальное имя или уникальный host |
| Узел | `POST /admin/nodes` | уникальный `node_key` |
| Endpoint | `POST /admin/server-endpoints` | полный уникальный набор полей endpoint |

## Важное ограничение

Обычная повторная отправка формы проверяет обработку дубликата, но не доказывает
защиту от гонки. Для строгой проверки два запроса должны стартовать одновременно на
настоящем PostgreSQL. Эту проверку выполняет отдельный integration-тест из проекта.

## Вариант 1 — строгая автоматическая проверка

Команды ниже рассчитаны на Linux Mint и выполняются из корня репозитория
`ProjectVPN`. Нужны Git, Docker и Python 3.12 или новее.

### 1. Получить актуальный `main`

Если репозиторий ещё не скачан:

```bash
git clone --branch main --single-branch https://github.com/Military05/ProjectVPN.git
cd ProjectVPN
```

Если он уже скачан:

```bash
cd ProjectVPN
git switch main
git pull --ff-only origin main
```

Убедитесь, что нет случайных локальных изменений:

```bash
git status
```

### 2. Проверить необходимые программы

```bash
git --version
docker --version
docker compose version
python3 --version
```

### 3. Установить зависимости проекта в отдельное окружение

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

### 4. Запустить отдельный PostgreSQL только для теста CHANGE-12

Сначала убедитесь, что контейнера с таким именем ещё нет:

```bash
docker ps -a --filter name=projectvpn-change12-postgres
```

Если список пуст, запустите тестовую базу:

```bash
docker run --rm -d \
  --name projectvpn-change12-postgres \
  -e POSTGRES_DB=projectvpn_change12 \
  -e POSTGRES_USER=change12_test \
  -e POSTGRES_PASSWORD=change12-local-only \
  -p 127.0.0.1:55432:5432 \
  postgres:16-alpine
```

Проверьте готовность:

```bash
docker exec projectvpn-change12-postgres \
  pg_isready -U change12_test -d projectvpn_change12
```

Должно появиться сообщение `accepting connections`. Если база ещё запускается,
повторите команду через несколько секунд.

### 5. Запустить целевые тесты

```bash
export PROJECTVPN_CHANGE12_TEST_DATABASE_URL='postgresql+asyncpg://change12_test:change12-local-only@127.0.0.1:55432/projectvpn_change12'

.venv/bin/python -m pytest -q \
  tests/unit/test_admin_creation_race_contract.py \
  tests/integration/test_admin_creation_postgresql.py
```

Нормальный результат:

- `10` unit-тестов проходят;
- `6` PostgreSQL integration-тестов проходят;
- PostgreSQL integration-тесты не пропущены;
- тест гонки получает только набор статусов `[201, 409]`;
- проверки количества строк получают `1`;
- тесты rollback и немаскированных ограничений проходят.

Если написано `SKIPPED`, переменная
`PROJECTVPN_CHANGE12_TEST_DATABASE_URL` не была передана процессу pytest.

### 6. Запустить общие проверки проекта

```bash
.venv/bin/python -m compileall -q src tests alembic
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
node --check src/shop_bot/apps/api/static/admin/assets/app.js
```

При полном `pytest` отдельно сравните результат с baseline, записанным в
`IMPLEMENTATION_PROGRESS_RU.md`. У проекта могут оставаться старые падения, поэтому
важно проверить, что список падений не расширился после CHANGE-12.

### 7. Остановить отдельную тестовую базу

```bash
docker stop projectvpn-change12-postgres
```

Контейнер запущен с `--rm`, поэтому после остановки его тестовые данные удалятся.

## Вариант 2 — посмотреть поведение через административную панель

Этот вариант удобен для визуальной проверки, но не заменяет конкурентный
integration-тест.

### 1. Подготовить локальные настройки

```bash
cp .env.example .env
nano .env
```

Для локального запуска достаточно заменить тестовые значения как минимум у:

```dotenv
POSTGRES_PASSWORD=local-only-strong-password
DATABASE_URL=postgresql+asyncpg://shopbot:local-only-strong-password@postgres:5432/shopbot
INTERNAL_API_KEY=local-only-internal-key
ADMIN_API_TOKEN=local-only-admin-token
NODE_AGENT_SHARED_SECRET=local-only-node-secret
DEMO_NODE_SHARED_SECRET=local-only-node-secret
BOT_TOKEN=123456:local-placeholder
AUTO_SEED_DEMO_DATA=false
DEMO_NODE_AUTO_REGISTER=false
PANEL_MODE=stub
NODE_AGENT_RUNTIME_MODE=stub
```

Не используйте в `.env` настоящие production-токены. Файл `.env` не должен
попадать в Git.

### 2. Запустить только нужные локальные сервисы

```bash
docker compose -p projectvpn-change12-check up -d --build \
  postgres redis node-agent migrate api
```

Проверить состояние:

```bash
docker compose -p projectvpn-change12-check ps
docker compose -p projectvpn-change12-check logs --tail=100 migrate api
curl -i http://localhost:8080/health/live
```

Ожидается HTTP `200` от health endpoint и успешно завершённый контейнер `migrate`.
Если вместо этого API пишет ошибку `_IncludedRouter`, смотрите предупреждение об
унаследованном блокере выше; не пытайтесь скрывать её перезапусками.

### 3. Открыть панель

Откройте в браузере:

```text
http://localhost:8080/admin-ui
```

Введите значение `ADMIN_API_TOKEN` из вашего локального `.env`.

### 4. Что нажать и что увидеть

Для каждого объекта используйте новое тестовое имя, например с текущей датой и
временем.

1. Создайте тариф. Он должен один раз появиться в списке.
2. Не меняя имя, отправьте форму повторно. Панель должна показать конфликт, а не
   сообщение о внутренней ошибке сервера.
3. Аналогично проверьте сервер по одинаковому имени и отдельно по одинаковому host.
4. Создайте выключенный тестовый узел (`is_enabled=false`), чтобы worker не пытался
   обращаться к нему. Повторите создание с тем же `node_key`.
5. Создайте два одинаковых endpoint для одного сервера. Второй запрос должен дать
   конфликт даже тогда, когда необязательные поля оставлены пустыми (`NULL`).

### 5. Проверить количество строк в PostgreSQL

Замените значения после `WHERE` на использованные вами тестовые значения:

```bash
docker compose -p projectvpn-change12-check exec -T postgres \
  psql -U shopbot -d shopbot -c \
  "SELECT count(*) FROM tariffs WHERE tariff_name = 'change12-tariff-example';"

docker compose -p projectvpn-change12-check exec -T postgres \
  psql -U shopbot -d shopbot -c \
  "SELECT count(*) FROM nodes WHERE node_key = 'change12-node-example';"

docker compose -p projectvpn-change12-check exec -T postgres \
  psql -U shopbot -d shopbot -c \
  "SELECT count(*) FROM node_credentials c JOIN nodes n ON n.node_id = c.node_id WHERE n.node_key = 'change12-node-example';"
```

Каждый запрос должен вернуть `count = 1`.

### 6. Остановить локальный стенд

Сохранить тестовые данные:

```bash
docker compose -p projectvpn-change12-check down
```

Полностью удалить только данные этого тестового стенда:

```bash
docker compose -p projectvpn-change12-check down --volumes
```

Команду с `--volumes` выполняйте только если эти тестовые данные больше не нужны.

## Итоговый чек-лист приёмки

- [ ] Unit-тесты CHANGE-12 прошли.
- [ ] PostgreSQL integration-тесты действительно запустились, а не были пропущены.
- [ ] Для одновременного создания тарифа результат — один `201` и один `409`.
- [ ] Для одновременного создания сервера результат — один `201` и один `409`.
- [ ] Для одновременного создания узла результат — один `201` и один `409`.
- [ ] В базе ровно один узел и ровно один credential этого узла.
- [ ] Для двух одинаковых endpoint результат — один `201` и один `409`, включая
      вариант с пустыми необязательными полями.
- [ ] Ошибка при создании спецификации тарифа откатывает сам тариф.
- [ ] Ошибка при создании credential откатывает сам узел.
- [ ] Нарушения `CHECK` и `FOREIGN KEY` не возвращаются как ложный `409`.
- [ ] `compileall` прошёл.
- [ ] В полном `pytest` не появилось новых падений относительно baseline.
- [ ] Health и административная панель не падают в middleware с `_IncludedRouter`.
- [ ] `.env`, реальные токены и пароли не попали в Git.

CHANGE-12 можно считать полностью подтверждённым только после выполнения всех
пунктов этого списка на настоящем PostgreSQL.
