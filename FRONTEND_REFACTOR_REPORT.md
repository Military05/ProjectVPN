# Frontend refactor report

Дата: 2026-04-26

## 1. Первичный аудит

Проект оказался не классическим frontend-приложением, а backend-first сервисом:

- backend/API: FastAPI;
- bot: aiogram;
- worker: arq;
- database: PostgreSQL через SQLAlchemy Core/asyncpg;
- cache/queue: Redis;
- node-agent: FastAPI;
- frontend framework: отсутствовал;
- routing frontend: отсутствовал;
- state management frontend: отсутствовал;
- CSS-подход: отсутствовал;
- сборщик frontend: отсутствовал;
- существующий UI: только минимальная HTML-страница dummy payment sandbox.

Административные сценарии уже были представлены backend API:

- тарифы: `/admin/tariffs`;
- серверы: `/admin/servers`;
- endpoint'ы серверов: `/admin/server-endpoints`;
- подписки: `/admin/subscriptions`;
- VPN-конфиги: `/admin/vpn-configurations`;
- платежи: `/admin/payment-orders`;
- узлы: `/admin/nodes`;
- задачи узлов: `/admin/nodes/tasks`;
- health: `/health/live`, `/health/ready`.

## 2. Основные UX/UI-проблемы до изменений

- Не было полноценной admin-панели для ежедневной работы администратора.
- Управление сущностями было возможно только через API/docs.
- Не было dashboard с ключевыми метриками.
- Не было предсказуемой навигации по сущностям.
- Не было loading, empty, error и success states.
- Не было единой системы кнопок, карточек, таблиц, бейджей и форм.
- Не было клиентских проверок форм до отправки.
- Не было подтверждений для опасных действий.
- Тестовая sandbox-страница оплаты выглядела как технический HTML без единого дизайн-языка.
- Не было мобильной и планшетной адаптации admin-интерфейса.
- Не было accessibility-слоя для клавиатуры, focus states, labels, aria.

## 3. Принятые инженерные решения

Так как в проекте не было frontend build pipeline, новая панель сделана как lightweight vanilla SPA,
которая обслуживается FastAPI из `/admin-ui`. Это сохраняет backend-архитектуру, не добавляет тяжелых
зависимостей, не ломает Docker/CLI-команды и не меняет существующие API-контракты.

Токен администратора не переносился в cookies и не менял security flow. Панель использует существующий
контракт `X-Admin-Token: <ADMIN_API_TOKEN>` и хранит токен только в `sessionStorage` текущей сессии.

## 4. Что реализовано

### Новая admin-панель

- Страница входа по Admin API token.
- App layout с sidebar, topbar, page container и responsive mobile drawer.
- Dashboard с метриками, предупреждениями, быстрыми действиями и health-индикаторами.
- Клиенты, собранные из существующих подписок, платежей и VPN-конфигов без добавления нового user API.
- Страницы тарифов, серверов, endpoint'ов, узлов, платежей, подписок, VPN-конфигов, задач узлов, системы и справки.
- Поиск, фильтры, pagination и row actions для списков.
- Модальные формы создания тарифов, серверов, endpoint'ов и узлов.
- Подтверждения для ручной оплаты, provisioning, revoke, sync, dispatch и reconcile.
- Диалог с одноразовым shared secret после создания узла.

### Design system

Добавлены design tokens:

- цвета;
- spacing;
- radii;
- shadows;
- typography;
- z-index;
- transitions;
- status colors.

Добавлены reusable UI-паттерны:

- Button;
- Input;
- Select;
- Textarea;
- Switch;
- Modal/Dialog;
- Card;
- Badge;
- Tooltip pattern через подсказки и helper text;
- Table;
- Tabs;
- Alert;
- Toast;
- Skeleton;
- EmptyState;
- ErrorState;
- PageHeader;
- SectionHeader;
- ConfirmDialog pattern.

### UX-состояния

- loading skeletons;
- empty states с объяснением и следующим шагом;
- error states с retry;
- success/error/warning/info notifications;
- disabled/pending states для submit/action кнопок;
- hover/focus/active/selected states;
- readable status badges.

### Accessibility

- skip link;
- semantic `main`, `nav`, `header`;
- aria-label для важных controls;
- labels и helper text для inputs;
- keyboard Escape для закрытия модалок;
- видимые focus states;
- touch-friendly размеры кнопок;
- адаптивный sidebar drawer на мобильных.

## 5. Применение UX-принципов

- Закон Хика-Хаймана: действия сгруппированы по страницам, основной CTA вынесен в PageHeader, редкие действия спрятаны в контекстные кнопки.
- Закон Фиттса: крупные CTA, touch-friendly высоты, опасные действия отделены подтверждением.
- Закон Миллера: таблицы разбиты pagination, формы разбиты на логические секции, dashboard показывает только ключевые метрики.
- Recognition over recall: активный раздел, понятные labels, helper text, status badges, empty states.
- Эвристики Нильсена: видимость загрузки/ошибки/успеха, предсказуемые паттерны, подтверждение опасных действий.
- Гештальт: единые карточки, таблицы, spacing, визуальные границы блоков.
- Закон Якоба: стандартный admin-dashboard pattern с left sidebar, topbar, таблицами, фильтрами и модалками.
- Progressive disclosure: технические параметры endpoint'ов и узлов вынесены в формы и подсказки, dashboard не перегружен.
- Error prevention/recovery: клиентская валидация, disabled pending state, retry, человеческие тексты ошибок.
- Aesthetic-usability effect: единая нейтральная светлая тема, спокойные акценты, аккуратные состояния.

## 6. Изменённые файлы

- `README.md`
- `pyproject.toml`
- `src/shop_bot/apps/api/main.py`
- `src/shop_bot/apps/api/routes/admin_ui.py`
- `src/shop_bot/apps/api/routes/sandbox.py`
- `src/shop_bot/apps/api/static/admin/index.html`
- `src/shop_bot/apps/api/static/admin/assets/styles.css`
- `src/shop_bot/apps/api/static/admin/assets/app.js`
- `FRONTEND_REFACTOR_REPORT.md`

## 7. Что стало ближе к vless-shopbot

- Появилась полноценная web-панель вместо API-only управления.
- Добавлены dashboard, entities management, таблицы, настройки, статусы и быстрые действия.
- Логика панели ориентирована на администратора VPN/бот-сервиса: тарифы, пользователи/клиенты, серверы, платежи, подписки, VPN-доступы.
- Управление вынесено в привычную admin-структуру, похожую по смыслу на panel-first подход референса.

## 8. Улучшения сверх референса

- Панель не требует отдельного frontend build step.
- Добавлены более явные empty/error/loading states.
- Добавлены guarded actions и человеческие ошибки.
- Добавлены responsive drawer и touch-friendly controls.
- Добавлены более строгие design tokens и единый компонентный язык.
- Добавлены system health и multinode/task views, учитывающие текущую архитектуру проекта.

## 9. Проверки

Выполнено:

- `python -S -m compileall -q src tests` — успешно;
- `node --check src/shop_bot/apps/api/static/admin/assets/app.js` — успешно;
- TOML parse для `pyproject.toml` — успешно;
- unit tests: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ... pytest -q tests/unit -p pytest_asyncio.plugin` — `10 passed in 0.37s`.

Ограничения проверки:

- обычный запуск `pytest -q` в данном контейнере зависал/не завершался из-за поведения Python окружения/автозагружаемых плагинов;
- `ruff` CLI в окружении не найден, поэтому полноценный lint не запускался;
- runtime import `create_app()` в контейнере не выполнен из-за отсутствующей pre-existing зависимости `arq` в окружении, поэтому для Python-кода выполнена синтаксическая проверка через `compileall`.

## 10. Оставшиеся рекомендации

- Добавить backend CRUD/update/delete endpoints для сущностей, если продукту нужно полноценное редактирование из панели.
- Добавить отдельный users/customers endpoint, чтобы не собирать клиентов косвенно из подписок и платежей.
- Добавить server-side pagination/filtering для больших таблиц.
- Добавить audit log API и вывести журнал действий в панели.
- Добавить e2e-тесты admin UI через Playwright после появления стабильного frontend test pipeline.
- Добавить CSP/security headers для production-развертывания static admin UI.
