# Локальный запуск и полная проверка ProjectVPN

Эта инструкция рассчитана на актуальную ветку `main`. Она подходит для Linux Mint,
Ubuntu и другого Linux с Docker Compose v2.

Последний автоматический acceptance:

- GitHub Actions: [`35254302019`](https://github.com/Military05/ProjectVPN/actions/runs/35254302019);
- полный набор на PostgreSQL 16 и Redis: `337 passed`, `0 skipped`;
- `docker compose build --no-cache`, миграция, API, worker, node-agent,
  Prometheus, admin UI/API и сборка bot успешно проверены.

## 1. Что установить

Понадобятся:

- Git;
- Docker Engine и Docker Compose v2;
- Python 3.12 — только если вы хотите запускать тесты с ноутбука;
- Node.js — только для отдельной синтаксической проверки JavaScript панели.

Проверка:

```bash
git --version
docker --version
docker compose version
python3 --version
```

Пользователь должен иметь право запускать Docker. Если команда требует `sudo`,
либо используйте `sudo`, либо настройте Docker-группу по документации вашей ОС.

## 2. Скачать или обновить проект

Первое скачивание:

```bash
git clone --branch main --single-branch https://github.com/Military05/ProjectVPN.git
cd ProjectVPN
```

Если проект уже скачан:

```bash
cd ProjectVPN
git switch main
git pull --ff-only origin main
git status
```

Перед продолжением `git status` не должен показывать случайные изменения.

## 3. Создать локальный `.env`

```bash
cp .env.example .env
nano .env
```

Минимально замените следующие значения:

```dotenv
APP_ENV=dev
POSTGRES_PASSWORD=СЛУЧАЙНЫЙ_ПАРОЛЬ_БАЗЫ
DATABASE_URL=postgresql+asyncpg://shopbot:СЛУЧАЙНЫЙ_ПАРОЛЬ_БАЗЫ@postgres:5432/shopbot
INTERNAL_API_KEY=СЛУЧАЙНЫЙ_ВНУТРЕННИЙ_КЛЮЧ
ADMIN_API_TOKEN=СЛУЧАЙНЫЙ_ТОКЕН_АДМИНА
NODE_AGENT_SHARED_SECRET=СЛУЧАЙНЫЙ_КЛЮЧ_УЗЛА
DEMO_NODE_SHARED_SECRET=ТОТ_ЖЕ_СЛУЧАЙНЫЙ_КЛЮЧ_УЗЛА
PANEL_MODE=stub
NODE_AGENT_RUNTIME_MODE=stub
NODE_AGENT_INBOUND_ID=1
PAYMENT_DEFAULT_PROVIDER=dummy
AUTO_SEED_DEMO_DATA=true
DEMO_NODE_AUTO_REGISTER=true
```

Случайные значения можно получить так:

```bash
openssl rand -hex 32
```

Для настоящего Telegram-бота укажите токен от BotFather:

```dotenv
BOT_TOKEN=123456789:НАСТОЯЩИЙ_ТОКЕН_ОТ_BOTFATHER
```

Если токена пока нет, не запускайте сервис `bot`. Остальные компоненты и
административная панель работают без обращения к Telegram.

Проверьте, что `.env` игнорируется Git:

```bash
git check-ignore .env
```

Команда должна вывести `.env`. Никогда не добавляйте этот файл в commit.

## 4. Чистая сборка

```bash
docker compose build --no-cache
```

Сборка устанавливает только зависимости из hash-pinned lock-файлов и выполняет
`pip check` внутри образа.

## 5. Запустить миграцию и основные сервисы

Сначала база, Redis, node-agent и одноразовая миграция:

```bash
docker compose up -d postgres redis node-agent migrate
docker compose ps -a
docker compose logs --tail=100 migrate
```

У `migrate` должен быть статус `Exited (0)`.

Затем API, worker и Prometheus:

```bash
docker compose up -d api worker prometheus
```

Если в `.env` находится настоящий `BOT_TOKEN`, запустите Telegram-бот:

```bash
docker compose up -d bot
```

Итоговое состояние:

```bash
docker compose ps -a
docker compose logs --tail=100 api worker node-agent bot
```

API, worker, node-agent, PostgreSQL, Redis и Prometheus должны работать;
`migrate` должен быть успешно завершён. Bot должен работать только с действительным
Telegram-токеном.

## 6. Автоматически проверить запущенный стек

```bash
curl -fsS http://localhost:8080/health/live
curl -fsS http://localhost:8080/health/ready
curl -fsS http://localhost:8090/openapi.json >/dev/null
curl -fsS http://localhost:9090/-/ready
```

Ожидаемые ответы API:

```json
{"status":"ok"}
{"status":"ready"}
```

Проверка версии схемы и Redis:

```bash
docker compose exec -T postgres \
  psql -U shopbot -d shopbot -tAc 'SELECT version_num FROM alembic_version'

docker compose exec -T redis redis-cli ping
```

Ожидается:

```text
20260913_0010
PONG
```

Проверка административного API — подставьте свой `ADMIN_API_TOKEN`:

```bash
curl -fsS \
  -H 'X-Admin-Token: ВАШ_ADMIN_API_TOKEN' \
  http://localhost:8080/admin/tariffs

curl -i -fsS \
  -H 'X-Admin-Token: ВАШ_ADMIN_API_TOKEN' \
  'http://localhost:8080/admin/nodes?limit=50&offset=0'
```

Во втором ответе должны присутствовать заголовки `X-Page-Limit: 50`,
`X-Page-Offset: 0` и `X-Has-More`.

## 7. Проверить административную панель вручную

1. Откройте `http://localhost:8080/admin-ui`.
2. Введите `ADMIN_API_TOKEN` из `.env`.
3. Убедитесь, что открывается Dashboard и нет красных ошибок загрузки.
4. Перейдите по разделам «Тарифы», «Серверы», «Узлы», «Платежи»,
   «Подписки», «VPN-конфиги» и «Задачи».
5. Создайте тестовый тариф.
6. Создайте тестовый сервер с уникальным именем и host.
7. Создайте выключенный тестовый узел (`is_enabled=false`).
8. Создайте endpoint для тестового сервера.
9. Повторите создание с тем же уникальным полем: панель должна показать конфликт,
   API должен вернуть `409`, а не `500`.
10. Если записей больше 50, проверьте кнопки «Назад» и «Вперёд».

Для проверки только конкурентного создания по `CHANGE-12` используйте отдельный
файл [`CHANGE12_CHECKLIST_RU.md`](CHANGE12_CHECKLIST_RU.md).

## 8. Проверить Telegram-бота

Этот шаг требует настоящий `BOT_TOKEN` и доступ в интернет.

```bash
docker compose up -d bot
docker compose logs -f bot
```

В Telegram отправьте боту:

```text
/start
/menu
/plans
/status
/instructions
/help
```

Проверьте открытие меню и тарифов. Ошибка `Unauthorized` означает, что токен
неверный или отозван; это не исправляется пересборкой контейнера.

## 9. Полный pytest с настоящими PostgreSQL и Redis

Этот раздел нужен, если вы хотите локально повторить GitHub Actions и получить
ноль skipped. Тестовые контейнеры используют отдельные имена, порты и данные.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements/dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m pip check
```

Запустите одноразовые тестовые сервисы:

```bash
docker run --rm -d \
  --name projectvpn-test-postgres \
  -e POSTGRES_DB=projectvpn_acceptance \
  -e POSTGRES_USER=shopbot \
  -e POSTGRES_PASSWORD=local-test-password \
  -p 127.0.0.1:55432:5432 \
  postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777

docker run --rm -d \
  --name projectvpn-test-redis \
  -p 127.0.0.1:56379:6379 \
  redis:7-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2
```

Дождитесь готовности:

```bash
docker exec projectvpn-test-postgres \
  pg_isready -U shopbot -d projectvpn_acceptance
docker exec projectvpn-test-redis redis-cli ping
```

Передайте адреса всем трём PostgreSQL-наборам:

```bash
export TEST_DATABASE_URL='postgresql+asyncpg://shopbot:local-test-password@127.0.0.1:55432/projectvpn_acceptance'
export DATABASE_URL="$TEST_DATABASE_URL"
export REDIS_URL='redis://127.0.0.1:56379/0'
export PROJECTVPN_CHANGE03_TEST_DATABASE_URL="$TEST_DATABASE_URL"
export PROJECTVPN_CHANGE12_TEST_DATABASE_URL="$TEST_DATABASE_URL"
export PROJECTVPN_MIGRATION_0010_TEST_DATABASE_URL="$TEST_DATABASE_URL"

.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tests alembic
.venv/bin/python -m pip check
node --check src/shop_bot/apps/api/static/admin/assets/app.js
```

Для текущего `main` ожидается `337 passed` и отсутствие `skipped`.

После проверки удалите только тестовые контейнеры:

```bash
docker stop projectvpn-test-postgres projectvpn-test-redis
unset TEST_DATABASE_URL DATABASE_URL REDIS_URL
unset PROJECTVPN_CHANGE03_TEST_DATABASE_URL
unset PROJECTVPN_CHANGE12_TEST_DATABASE_URL
unset PROJECTVPN_MIGRATION_0010_TEST_DATABASE_URL
```

## 10. Остановка, обновление и удаление данных

Остановить проект, сохранив PostgreSQL volume:

```bash
docker compose down
```

Обновить проект и снова применить миграции:

```bash
git switch main
git pull --ff-only origin main
docker compose build --no-cache
docker compose up -d postgres redis node-agent migrate
docker compose up -d api worker prometheus
docker compose up -d bot
```

Полностью удалить локальные данные проекта:

```bash
docker compose down --volumes --remove-orphans
```

Команда с `--volumes` необратимо удаляет локальную базу ProjectVPN. Не запускайте
её на нужных данных без резервной копии.

Резервная копия PostgreSQL:

```bash
docker compose exec -T postgres \
  pg_dump -U shopbot -d shopbot -Fc > projectvpn-backup.dump
```

## 11. Что изменить перед production

Локальные `stub` и `dummy` предназначены только для разработки. Перед реальным
развёртыванием необходимо:

1. поставить `APP_ENV=production`;
2. заменить все `change-me`, демонстрационные и локальные секреты;
3. выбрать и настроить настоящий платёжный провайдер;
4. настроить настоящий XUI/panel и положительный числовой `NODE_AGENT_INBOUND_ID`;
5. отключить `AUTO_SEED_DEMO_DATA` и `DEMO_NODE_AUTO_REGISTER`;
6. использовать настоящий Telegram token;
7. настроить HTTPS/reverse proxy, резервные копии и мониторинг;
8. выполнить отдельный staging smoke платежа, выдачи и отзыва VPN.

Production validator намеренно не даст стартовать с dummy/stub и очевидными
placeholder-секретами.

## 12. Если что-то не запускается

Сначала соберите диагностику:

```bash
docker compose ps -a
docker compose logs --tail=200 migrate api worker bot node-agent postgres redis
curl -i http://localhost:8080/health/live
curl -i http://localhost:8080/health/ready
```

Частые причины:

- `Unauthorized` у bot — неверный `BOT_TOKEN`;
- `health/ready = 503` — база, Redis или схема ещё не готовы;
- `migrate` завершился не с кодом `0` — изучите его лог до запуска worker;
- `_IncludedRouter` — используется старый образ; выполните
  `docker compose build --no-cache` из актуального `main`;
- порт `8080`, `8090` или `9090` занят другим процессом;
- пароль в `POSTGRES_PASSWORD` не совпадает с паролем внутри `DATABASE_URL`.

Автоматический эталон всех проверок находится в
`.github/workflows/final-acceptance.yml`.
