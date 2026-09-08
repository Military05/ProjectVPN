(() => {
  "use strict";

  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

  class ApiProblem extends Error {
    constructor(message, status = 0, detail = null) {
      super(message);
      this.name = "ApiProblem";
      this.status = status;
      this.detail = detail;
    }
  }

  const STORAGE_KEY = "shopbot-admin-token";
  const DATASETS = [
    ["tariffs", "/admin/tariffs"],
    ["servers", "/admin/servers"],
    ["endpoints", "/admin/server-endpoints"],
    ["subscriptions", "/admin/subscriptions"],
    ["vpn", "/admin/vpn-configurations"],
    ["payments", "/admin/payment-orders"],
    ["nodes", "/admin/nodes"],
    ["tasks", "/admin/nodes/tasks"],
    ["live", "/health/live", { public: true }],
    ["ready", "/health/ready", { public: true }],
  ];

  const NAV_ITEMS = [
    { key: "dashboard", label: "Dashboard", icon: "⌁", title: "Dashboard", kicker: "Обзор" },
    { key: "clients", label: "Клиенты", icon: "👥", title: "Клиенты", kicker: "Пользователи" },
    { key: "servers", label: "Серверы", icon: "▦", title: "Серверы", kicker: "Инфраструктура" },
    { key: "nodes", label: "Узлы", icon: "◆", title: "Узлы", kicker: "Multinode" },
    { key: "plans", label: "Тарифы", icon: "₽", title: "Тарифы", kicker: "Продажи" },
    { key: "payments", label: "Платежи", icon: "↗", title: "Платежи", kicker: "Финансы" },
    { key: "subscriptions", label: "Подписки", icon: "◴", title: "Подписки", kicker: "Доступ" },
    { key: "vpn", label: "VPN-конфиги", icon: "⌘", title: "VPN-конфиги", kicker: "Доступы" },
    { key: "tasks", label: "Задачи", icon: "↻", title: "Задачи узлов", kicker: "Очередь" },
    { key: "system", label: "Система", icon: "⚙", title: "Система", kicker: "Настройки" },
    { key: "help", label: "Справка", icon: "?", title: "Справка", kicker: "Документация" },
  ];

  const state = {
    token: sessionStorage.getItem(STORAGE_KEY) || "",
    route: "dashboard",
    loading: false,
    data: {},
    errors: {},
    search: {},
    filters: {},
    pagination: {},
    lastLoadedAt: null,
  };

  const PAGE_RENDERERS = {
    dashboard: renderDashboard,
    clients: renderClients,
    servers: renderServers,
    nodes: renderNodes,
    plans: renderPlans,
    payments: renderPayments,
    subscriptions: renderSubscriptions,
    vpn: renderVpn,
    tasks: renderTasks,
    system: renderSystem,
    help: renderHelp,
  };

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    renderNav();
    bindGlobalEvents();
    state.route = getRouteFromHash();
    window.addEventListener("hashchange", () => {
      state.route = getRouteFromHash();
      closeMobileMenu();
      renderRoute();
    });

    if (state.token) {
      showApp();
      loadAllData().then(renderRoute);
    } else {
      showLogin();
    }
  }

  function bindGlobalEvents() {
    $("#login-form").addEventListener("submit", handleLogin);
    $("#logout-button").addEventListener("click", logout);
    $("#refresh-button").addEventListener("click", () => refreshData(true));
    $("#mobile-menu-button").addEventListener("click", openMobileMenu);
    $("#mobile-backdrop").addEventListener("click", closeMobileMenu);

    $("#main-content").addEventListener("click", handleMainClick);
    $("#main-content").addEventListener("input", handleMainInput);
    $("#main-content").addEventListener("change", handleMainChange);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && $("#modal-root").innerHTML.trim()) {
        closeDialog();
      }
    });
  }

  async function handleLogin(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = $("#admin-token");
    const token = input.value.trim();
    if (!token) {
      showToast("danger", "Введите токен", "Admin API token обязателен для доступа к панели.");
      input.focus();
      return;
    }

    const submit = form.querySelector("button[type='submit']");
    setButtonPending(submit, true, "Проверяем...");
    state.token = token;
    try {
      await apiRequest("/admin/tariffs");
      sessionStorage.setItem(STORAGE_KEY, token);
      showApp();
      showToast("success", "Доступ открыт", "Токен принят. Загружаю данные панели.");
      await loadAllData();
      renderRoute();
    } catch (error) {
      state.token = "";
      sessionStorage.removeItem(STORAGE_KEY);
      showToast("danger", "Не удалось войти", humanizeError(error));
      input.focus();
    } finally {
      setButtonPending(submit, false);
    }
  }

  function showLogin() {
    $("#login-view").classList.remove("is-hidden");
    $("#app-shell").classList.add("is-hidden");
    setTimeout(() => $("#admin-token")?.focus(), 0);
  }

  function showApp() {
    $("#login-view").classList.add("is-hidden");
    $("#app-shell").classList.remove("is-hidden");
  }

  function logout() {
    state.token = "";
    state.data = {};
    state.errors = {};
    sessionStorage.removeItem(STORAGE_KEY);
    closeMobileMenu();
    showLogin();
    showToast("info", "Вы вышли", "Токен удалён из текущей сессии браузера.");
  }

  async function refreshData(showMessage = false) {
    const button = $("#refresh-button");
    setButtonPending(button, true, "Обновляем...");
    try {
      await loadAllData();
      renderRoute();
      if (showMessage) {
        showToast("success", "Данные обновлены", "Панель получила актуальные данные из API.");
      }
    } catch (error) {
      showToast("danger", "Ошибка обновления", humanizeError(error));
    } finally {
      setButtonPending(button, false);
    }
  }

  async function loadAllData() {
    if (!state.token) {
      return;
    }
    state.loading = true;
    renderRoute();

    const results = await Promise.all(
      DATASETS.map(async ([key, path, options]) => {
        try {
          const data = await apiRequest(path, {}, options);
          return { key, data };
        } catch (error) {
          return { key, error };
        }
      }),
    );

    const nextErrors = {};
    for (const result of results) {
      if (result.error) {
        nextErrors[result.key] = result.error;
        if (!Array.isArray(state.data[result.key])) {
          state.data[result.key] = result.key === "live" || result.key === "ready" ? null : [];
        }
      } else {
        state.data[result.key] = result.data;
      }
    }
    state.errors = nextErrors;
    state.lastLoadedAt = new Date();
    state.loading = false;

    if (Object.values(nextErrors).some((error) => error.status === 401)) {
      logout();
      showToast("danger", "Сессия не авторизована", "Проверьте ADMIN_API_TOKEN и войдите снова.");
    }
  }

  async function apiRequest(path, options = {}, endpointOptions = {}) {
    const headers = new Headers(options.headers || {});
    const hasBody = Boolean(options.body);
    if (hasBody && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    if (!endpointOptions.public && state.token) {
      headers.set("X-Admin-Token", state.token);
    }

    let response;
    try {
      response = await fetch(path, { ...options, headers });
    } catch (error) {
      throw new ApiProblem("Network error", 0, error);
    }

    const contentType = response.headers.get("content-type") || "";
    const raw = await response.text();
    let payload = raw;
    if (raw && contentType.includes("application/json")) {
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = raw;
      }
    }

    if (!response.ok) {
      const detail = payload && typeof payload === "object" ? payload.detail : payload;
      throw new ApiProblem(String(detail || response.statusText), response.status, detail);
    }
    return payload || null;
  }

  function renderNav() {
    const nav = $("#sidebar-nav");
    nav.innerHTML = NAV_ITEMS.map((item) => {
      const active = item.key === state.route ? " is-active" : "";
      return `
        <a class="nav-item${active}" href="#/${item.key}" aria-current="${active ? "page" : "false"}">
          <span class="nav-icon" aria-hidden="true">${item.icon}</span>
          <span>${escapeHtml(item.label)}</span>
        </a>
      `;
    }).join("");
  }

  function renderRoute() {
    if (!state.token) {
      return;
    }
    renderNav();
    const page = NAV_ITEMS.find((item) => item.key === state.route) || NAV_ITEMS[0];
    $("#page-title").textContent = page.title;
    $("#page-kicker").textContent = page.kicker;

    const main = $("#main-content");
    if (state.loading && !hasLoadedCoreData()) {
      main.innerHTML = renderSkeletonPage();
      return;
    }

    const renderer = PAGE_RENDERERS[state.route] || renderDashboard;
    main.innerHTML = renderer();
    main.focus({ preventScroll: true });
  }

  function hasLoadedCoreData() {
    return ["tariffs", "servers", "payments", "subscriptions"].some((key) => key in state.data);
  }

  function getRouteFromHash() {
    const route = window.location.hash.replace(/^#\/?/, "").trim() || "dashboard";
    return NAV_ITEMS.some((item) => item.key === route) ? route : "dashboard";
  }

  function openMobileMenu() {
    $("#sidebar").classList.add("is-open");
    $("#mobile-backdrop").hidden = false;
  }

  function closeMobileMenu() {
    $("#sidebar").classList.remove("is-open");
    $("#mobile-backdrop").hidden = true;
  }

  function handleMainClick(event) {
    const button = event.target.closest("[data-action]");
    if (!button) {
      return;
    }
    const action = button.dataset.action;
    const id = button.dataset.id;
    const secondaryId = button.dataset.secondaryId;

    const actions = {
      createTariff: openTariffDialog,
      createServer: openServerDialog,
      createEndpoint: openEndpointDialog,
      createNode: openNodeDialog,
      reconcile: () => confirmServerAction({
        title: "Запустить сверку подписок?",
        message: "Система проверит истёкшие подписки и поставит нужные задачи в очередь.",
        confirmLabel: "Запустить сверку",
        run: () => apiRequest("/admin/reconcile", { method: "POST" }),
        success: "Сверка подписок запущена.",
      }),
      markPaid: () => markPaymentPaid(Number(id)),
      provision: () => provisionSubscription(Number(id)),
      revokeVpn: () => revokeVpnConfiguration(Number(id)),
      syncNode: () => syncNode(Number(id)),
      dispatchTask: () => dispatchTask(Number(id)),
      retryLoad: () => refreshData(true),
      copyText: () => copyToClipboard(button.dataset.copy || ""),
      setTab: () => {
        setFilter(state.route, "tab", id);
        renderRoute();
      },
      clientProvision: () => provisionSubscription(Number(secondaryId)),
      setPage: () => {
        state.pagination[button.dataset.routePage] = Number(button.dataset.page || 1);
        renderRoute();
      },
    };

    if (actions[action]) {
      actions[action]();
    }
  }

  function handleMainInput(event) {
    const input = event.target.closest("[data-search]");
    if (!input) {
      return;
    }
    state.search[input.dataset.search] = input.value;
    state.pagination[input.dataset.search] = 1;
    renderRoute();
  }

  function handleMainChange(event) {
    const control = event.target.closest("[data-filter]");
    if (!control) {
      return;
    }
    const [route, key] = control.dataset.filter.split(":");
    setFilter(route, key, control.value);
    renderRoute();
  }

  function setFilter(route, key, value) {
    state.filters[route] = { ...(state.filters[route] || {}), [key]: value };
    state.pagination[route] = 1;
  }

  function getFilter(route, key, fallback = "all") {
    return state.filters[route]?.[key] || fallback;
  }

  function data(key) {
    const value = state.data[key];
    return Array.isArray(value) ? value : [];
  }

  function renderDashboard() {
    const tariffs = data("tariffs");
    const servers = data("servers");
    const endpoints = data("endpoints");
    const subscriptions = data("subscriptions");
    const vpn = data("vpn");
    const payments = data("payments");
    const nodes = data("nodes");
    const tasks = data("tasks");
    const paidPayments = payments.filter(isPaidOrder);
    const revenue = paidPayments.reduce((sum, order) => sum + Number(order.amount_minor || 0), 0);
    const activeSubscriptions = subscriptions.filter((item) => item.status === "active");
    const activeVpn = vpn.filter((item) => item.status === "active");
    const onlineNodes = nodes.filter((node) => getNodeStatus(node) === "online");
    const pendingTasks = tasks.filter((task) => ["pending", "retrying"].includes(String(task.status)));

    const warnings = [];
    if (!tariffs.length) warnings.push("Создайте первый тариф, чтобы бот мог продавать подписки.");
    if (!servers.length) warnings.push("Добавьте сервер с публичным VLESS host.");
    if (!endpoints.length) warnings.push("Добавьте endpoint, чтобы выдавать рабочие конфигурации.");
    if (nodes.length && onlineNodes.length === 0) warnings.push("Все узлы сейчас выглядят недоступными.");
    if (state.errors.ready) warnings.push("Readiness-проверка API не прошла: БД или Redis могут быть недоступны.");

    return `
      ${pageHeader({
        title: "Единая панель управления",
        description: "Ключевые метрики, быстрые действия и состояние multinode-инфраструктуры.",
        actions: `
          <button class="btn btn-primary" type="button" data-action="createTariff">Создать тариф</button>
          <button class="btn btn-secondary" type="button" data-action="createServer">Добавить сервер</button>
          <button class="btn btn-secondary" type="button" data-action="createNode">Добавить узел</button>
        `,
      })}
      ${renderErrorSummary()}
      ${warnings.length ? alertBox("warning", "Что требует внимания", warnings.join(" ")) : ""}
      <section class="grid-4" aria-label="Ключевые метрики">
        ${statCard("Активные подписки", activeSubscriptions.length, "Клиенты с активным доступом")}
        ${statCard("VPN-конфиги", activeVpn.length, "Активные ключи в системе")}
        ${statCard("Оплачено", formatMoney(revenue, payments[0]?.currency || "RUB"), `${paidPayments.length} успешных платежей`)}
        ${statCard("Узлы онлайн", `${onlineNodes.length}/${nodes.length || 0}`, `${pendingTasks.length} задач ожидают обработки`)}
      </section>
      <section class="grid-2 mt-5">
        <div class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Быстрые действия</h2>
              <p class="card-subtitle">Главные операции вынесены наверх, редкие действия спрятаны глубже.</p>
            </div>
          </div>
          <div class="card-body">
            <div class="grid-2">
              <button class="btn btn-primary" type="button" data-action="createEndpoint">Добавить endpoint</button>
              <button class="btn btn-secondary" type="button" data-action="reconcile">Сверить подписки</button>
              <a class="btn btn-secondary" href="#/payments">Проверить платежи</a>
              <a class="btn btn-secondary" href="#/tasks">Открыть очередь</a>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Состояние системы</h2>
              <p class="card-subtitle">Health checks и свежесть данных.</p>
            </div>
            ${state.data.ready && !state.errors.ready ? badge("ready", "success") : badge("attention", "warning")}
          </div>
          <div class="card-body kpi-list">
            ${kpiRow("API live", state.errors.live ? "Недоступно" : "OK", state.errors.live ? "danger" : "success")}
            ${kpiRow("API ready", state.errors.ready ? "Проверьте БД/Redis" : "OK", state.errors.ready ? "warning" : "success")}
            ${kpiRow("Серверы", `${servers.filter((server) => server.is_enabled).length}/${servers.length} включены`, "info")}
            ${kpiRow("Обновлено", state.lastLoadedAt ? formatDateTime(state.lastLoadedAt) : "ещё нет", "neutral")}
          </div>
        </div>
      </section>
      <section class="grid-2 mt-5">
        ${recentPaymentsCard(payments)}
        ${nodeOverviewCard(nodes, tasks)}
      </section>
    `;
  }

  function recentPaymentsCard(payments) {
    const items = payments.slice(0, 6);
    return `
      <div class="card">
        <div class="card-header">
          <div>
            <h2 class="card-title">Последние платежи</h2>
            <p class="card-subtitle">Фокус на статусе и ручном восстановлении.</p>
          </div>
          <a class="btn btn-ghost btn-sm" href="#/payments">Все платежи</a>
        </div>
        <div class="card-body">
          ${items.length ? `<div class="timeline">${items.map((order) => `
            <div class="timeline-item">
              <span class="status-dot status-${statusTone(order.status)}" aria-hidden="true"></span>
              <div class="timeline-content">
                <strong>${formatMoney(order.amount_minor, order.currency)} · заказ #${escapeHtml(order.payment_order_id)}</strong>
                <span>${humanStatus(order.status)} · пользователь #${escapeHtml(order.user_id)} · ${formatDateTime(order.created_at)}</span>
              </div>
            </div>
          `).join("")}</div>` : emptyState("Платежей пока нет", "Когда бот создаст первый заказ, он появится в этом блоке.", "↗")}
        </div>
      </div>
    `;
  }

  function nodeOverviewCard(nodes, tasks) {
    const items = nodes.slice(0, 6);
    return `
      <div class="card">
        <div class="card-header">
          <div>
            <h2 class="card-title">Узлы и очередь</h2>
            <p class="card-subtitle">Multinode-контур ближе к управлению сервисом, как в vless-shopbot.</p>
          </div>
          <a class="btn btn-ghost btn-sm" href="#/nodes">Все узлы</a>
        </div>
        <div class="card-body">
          ${items.length ? `<div class="timeline">${items.map((node) => `
            <div class="timeline-item">
              <span class="status-dot status-${statusTone(getNodeStatus(node))}" aria-hidden="true"></span>
              <div class="timeline-content">
                <strong>${escapeHtml(node.display_name || node.node_key)} ${badge(getNodeStatus(node))}</strong>
                <span>${escapeHtml(node.api_base_url || "API URL не задан")} · задач: ${tasks.filter((task) => Number(task.node_id) === Number(node.node_id)).length}</span>
              </div>
            </div>
          `).join("")}</div>` : emptyState("Узлы ещё не добавлены", "Добавьте node-agent, чтобы управлять несколькими 3x-ui серверами централизованно.", "◆", `<button class="btn btn-primary" type="button" data-action="createNode">Добавить узел</button>`)}
        </div>
      </div>
    `;
  }

  function renderClients() {
    const rows = buildClientRows();
    const filtered = applySearchAndStatus("clients", rows, (row) => [
      row.user_id,
      row.last_tariff,
      row.status,
    ].join(" "));
    return `
      ${pageHeader({
        title: "Клиенты",
        description: "Сводка по пользователям собрана из подписок, заказов и VPN-конфигураций без изменения API.",
        actions: `<a class="btn btn-secondary" href="#/subscriptions">Открыть подписки</a>`,
      })}
      ${filterBar("clients", { placeholder: "Поиск по user ID, тарифу или статусу", statusOptions: statusOptions(["all", "active", "ended", "cancelled", "paused"]) })}
      ${renderTable({
        route: "clients",
        rows: filtered,
        empty: emptyState("Клиенты пока не найдены", "После регистрации через Telegram здесь появится клиентская сводка.", "👥"),
        columns: [
          { label: "Клиент", render: (row) => titleCell(`Пользователь #${row.user_id}`, `${row.orders} заказов · ${row.configs} VPN-конфигов`) },
          { label: "Статус", render: (row) => badge(row.status) },
          { label: "Тариф", render: (row) => titleCell(row.last_tariff || "Тариф не найден", `${row.subscriptions} подписок`) },
          { label: "Последняя активность", render: (row) => formatDateTime(row.last_activity) },
          { label: "Действия", render: (row) => row.active_subscription_id ? `<button class="btn btn-secondary btn-sm" type="button" data-action="clientProvision" data-secondary-id="${row.active_subscription_id}">Выдать доступ</button>` : `<span class="muted">Нет активной подписки</span>` },
        ],
      })}
    `;
  }

  function buildClientRows() {
    const map = new Map();
    const ensure = (userId) => {
      const key = String(userId || "unknown");
      if (!map.has(key)) {
        map.set(key, {
          user_id: key,
          status: "unknown",
          last_tariff: "",
          subscriptions: 0,
          active_subscription_id: null,
          orders: 0,
          configs: 0,
          last_activity: null,
        });
      }
      return map.get(key);
    };

    data("subscriptions").forEach((subscription) => {
      const row = ensure(subscription.user_id);
      row.subscriptions += 1;
      row.status = subscription.status || row.status;
      row.last_tariff = subscription.tariff_name || row.last_tariff;
      row.last_activity = latestDate(row.last_activity, subscription.created_at, subscription.ended_at);
      if (subscription.status === "active") {
        row.active_subscription_id = subscription.subscription_id;
      }
    });

    data("payments").forEach((order) => {
      const row = ensure(order.user_id);
      row.orders += 1;
      row.last_activity = latestDate(row.last_activity, order.created_at, order.updated_at, order.paid_at);
    });

    const subscriptionToUser = new Map(data("subscriptions").map((item) => [String(item.subscription_id), item.user_id]));
    data("vpn").forEach((config) => {
      const userId = subscriptionToUser.get(String(config.subscription_id));
      if (!userId) return;
      const row = ensure(userId);
      row.configs += 1;
      row.last_activity = latestDate(row.last_activity, config.created_at, config.revoked_at);
    });

    return Array.from(map.values()).sort((a, b) => compareDateDesc(a.last_activity, b.last_activity));
  }

  function renderPlans() {
    const rows = applySearchAndStatus("plans", data("tariffs"), (row) => [
      row.tariff_name,
      row.description,
      row.currency,
      row.period_days,
      row.is_enabled ? "enabled active" : "disabled",
    ].join(" "));
    return `
      ${pageHeader({
        title: "Тарифы",
        description: "Понятные тарифы — главная точка монетизации. Форма создания держит базовые поля сверху, дополнительные ниже.",
        actions: `<button class="btn btn-primary" type="button" data-action="createTariff">Создать тариф</button>`,
      })}
      ${filterBar("plans", { placeholder: "Поиск по названию, цене, валюте", statusOptions: statusOptions(["all", "enabled", "disabled"]) })}
      ${renderTable({
        route: "plans",
        rows,
        empty: emptyState("Пока нет тарифов", "Создайте первый тариф, чтобы пользователи могли оформить подписку.", "₽", `<button class="btn btn-primary" type="button" data-action="createTariff">Создать тариф</button>`),
        columns: [
          { label: "Тариф", render: (row) => titleCell(row.tariff_name, row.description || "Описание не задано") },
          { label: "Стоимость", render: (row) => titleCell(formatMoney(row.price_minor, row.currency), `${row.period_days} дн.`) },
          { label: "Статус", render: (row) => badge(row.is_enabled ? "enabled" : "disabled") },
          { label: "ID", render: (row) => `#${escapeHtml(row.tariff_id)}` },
          { label: "Действия", render: () => `<span class="muted">Редактирование API пока не поддерживает</span>` },
        ],
      })}
    `;
  }

  function renderServers() {
    const tab = getFilter("servers", "tab", "servers");
    const serverRows = applySearchAndStatus("servers", data("servers"), (row) => [
      row.server_name,
      row.host,
      row.is_enabled ? "enabled" : "disabled",
    ].join(" "));
    const endpointRows = applySearchAndStatus("servers", data("endpoints"), (row) => [
      row.protocol,
      row.port,
      row.security,
      row.sni,
      row.is_enabled ? "enabled" : "disabled",
      row.node_id,
      row.local_inbound_id,
    ].join(" "));

    return `
      ${pageHeader({
        title: "Серверы и endpoints",
        description: "Сервер — публичный VLESS host. Endpoint описывает протокол, порт и привязку к node-agent.",
        actions: `
          <button class="btn btn-primary" type="button" data-action="createServer">Добавить сервер</button>
          <button class="btn btn-secondary" type="button" data-action="createEndpoint">Добавить endpoint</button>
        `,
      })}
      ${tabs("servers", tab, [
        ["servers", "Серверы"],
        ["endpoints", "Endpoints"],
      ])}
      ${filterBar("servers", { placeholder: "Поиск по host, protocol, node", statusOptions: statusOptions(["all", "enabled", "disabled"]) })}
      ${tab === "servers" ? renderTable({
        route: "servers",
        rows: serverRows,
        empty: emptyState("Серверы ещё не добавлены", "Добавьте публичный VLESS host, чтобы endpoint мог выдавать рабочие ссылки.", "▦", `<button class="btn btn-primary" type="button" data-action="createServer">Добавить сервер</button>`),
        columns: [
          { label: "Сервер", render: (row) => titleCell(row.server_name, row.host) },
          { label: "Статус", render: (row) => badge(row.is_enabled ? "enabled" : "disabled") },
          { label: "Endpoints", render: (row) => data("endpoints").filter((endpoint) => Number(endpoint.server_id) === Number(row.server_id)).length },
          { label: "Создан", render: (row) => formatDateTime(row.created_at) },
          { label: "ID", render: (row) => `#${escapeHtml(row.server_id)}` },
        ],
      }) : renderTable({
        route: "servers",
        rows: endpointRows,
        empty: emptyState("Endpoints ещё не добавлены", "Создайте endpoint: протокол, порт, security и optional привязку к node-agent.", "⌘", `<button class="btn btn-primary" type="button" data-action="createEndpoint">Добавить endpoint</button>`),
        columns: [
          { label: "Endpoint", render: (row) => titleCell(`${row.protocol || "vless"}:${row.port}`, `server #${row.server_id} · endpoint #${row.server_endpoint_id}`) },
          { label: "Security", render: (row) => titleCell(row.security || "standard", row.sni || "SNI не задан") },
          { label: "Node", render: (row) => row.node_id ? titleCell(`node #${row.node_id}`, row.local_inbound_id || "inbound не задан") : `<span class="muted">legacy panel</span>` },
          { label: "Статус", render: (row) => badge(row.is_enabled ? "enabled" : "disabled") },
          { label: "Детали", render: (row) => smallDetails([row.transport_type, row.flow, row.encryption].filter(Boolean).join(" · ") || "детали не заданы") },
        ],
      })}
    `;
  }

  function renderNodes() {
    const rows = applySearchAndStatus("nodes", data("nodes"), (row) => [
      row.display_name,
      row.node_key,
      row.api_base_url,
      getNodeStatus(row),
      row.is_enabled ? "enabled" : "disabled",
    ].join(" "));
    return `
      ${pageHeader({
        title: "Узлы",
        description: "Node-agent управляет локальным 3x-ui runtime и снижает связанность центрального API с серверами.",
        actions: `<button class="btn btn-primary" type="button" data-action="createNode">Добавить узел</button>`,
      })}
      ${filterBar("nodes", { placeholder: "Поиск по имени, ключу, URL", statusOptions: statusOptions(["all", "online", "offline", "unknown", "enabled", "disabled"]) })}
      ${renderTable({
        route: "nodes",
        rows,
        empty: emptyState("Узлы пока не добавлены", "Добавьте первый node-agent, чтобы включить multinode-режим.", "◆", `<button class="btn btn-primary" type="button" data-action="createNode">Добавить узел</button>`),
        columns: [
          { label: "Узел", render: (row) => titleCell(row.display_name || row.node_key, row.api_base_url) },
          { label: "Состояние", render: (row) => `${badge(getNodeStatus(row))}<span class="cell-subtitle">${escapeHtml(row.agent_version || "version unknown")}</span>` },
          { label: "Вес", render: (row) => row.selection_weight ?? 100 },
          { label: "Последний сигнал", render: (row) => formatDateTime(row.last_seen_at) },
          { label: "Действия", render: (row) => `<button class="btn btn-secondary btn-sm" type="button" data-action="syncNode" data-id="${row.node_id}">Синхронизировать</button>` },
        ],
      })}
    `;
  }

  function renderPayments() {
    const rows = applySearchAndStatus("payments", data("payments"), (row) => [
      row.payment_order_id,
      row.user_id,
      row.provider,
      row.status,
      row.currency,
      row.amount_minor,
    ].join(" "));
    return `
      ${pageHeader({
        title: "Платежи",
        description: "Заказы сгруппированы по статусу. Ручная отметка оплаты отделена подтверждением.",
        actions: `<button class="btn btn-secondary" type="button" data-action="reconcile">Сверить подписки</button>`,
      })}
      ${filterBar("payments", { placeholder: "Поиск по заказу, пользователю, провайдеру", statusOptions: statusOptions(["all", "created", "pending", "paid", "failed", "cancelled"]) })}
      ${renderTable({
        route: "payments",
        rows,
        empty: emptyState("Платежей пока нет", "Когда клиент оформит подписку, заказ появится здесь.", "↗"),
        columns: [
          { label: "Заказ", render: (row) => titleCell(`#${row.payment_order_id}`, `user #${row.user_id}`) },
          { label: "Провайдер", render: (row) => badge(row.provider || "unknown", "info") },
          { label: "Сумма", render: (row) => titleCell(formatMoney(row.amount_minor, row.currency), `${row.requested_period_days || "?"} дн.`) },
          { label: "Статус", render: (row) => badge(row.status) },
          { label: "Создан", render: (row) => formatDateTime(row.created_at) },
          { label: "Действия", render: (row) => isPaidOrder(row) ? `<span class="muted">Оплачен</span>` : `<button class="btn btn-secondary btn-sm" type="button" data-action="markPaid" data-id="${row.payment_order_id}">Отметить оплаченным</button>` },
        ],
      })}
    `;
  }

  function renderSubscriptions() {
    const rows = applySearchAndStatus("subscriptions", data("subscriptions"), (row) => [
      row.subscription_id,
      row.user_id,
      row.tariff_name,
      row.status,
    ].join(" "));
    return `
      ${pageHeader({
        title: "Подписки",
        description: "Сначала видно пользователя, тариф и статус. Выдача VPN-доступа — явное действие с подтверждением.",
        actions: `<button class="btn btn-secondary" type="button" data-action="reconcile">Сверить истёкшие</button>`,
      })}
      ${filterBar("subscriptions", { placeholder: "Поиск по user ID, тарифу, подписке", statusOptions: statusOptions(["all", "active", "ended", "cancelled", "paused"]) })}
      ${renderTable({
        route: "subscriptions",
        rows,
        empty: emptyState("Подписок пока нет", "Новые подписки появятся после успешного платежа или регистрации заказа.", "◴"),
        columns: [
          { label: "Подписка", render: (row) => titleCell(`#${row.subscription_id}`, `user #${row.user_id}`) },
          { label: "Тариф", render: (row) => titleCell(row.tariff_name || `tariff #${row.tariff_id}`, `tariff ID ${row.tariff_id}`) },
          { label: "Статус", render: (row) => badge(row.status) },
          { label: "Создана", render: (row) => formatDateTime(row.created_at) },
          { label: "Завершена", render: (row) => formatDateTime(row.ended_at) },
          { label: "Действия", render: (row) => row.status === "active" ? `<button class="btn btn-secondary btn-sm" type="button" data-action="provision" data-id="${row.subscription_id}">Выдать доступ</button>` : `<span class="muted">Недоступно</span>` },
        ],
      })}
    `;
  }

  function renderVpn() {
    const rows = applySearchAndStatus("vpn", data("vpn"), (row) => [
      row.vpn_configuration_id,
      row.subscription_id,
      row.display_name,
      row.client_uuid,
      row.status,
      row.remote_client_ref,
    ].join(" "));
    return `
      ${pageHeader({
        title: "VPN-конфиги",
        description: "Ключи читаются как управляемые сущности: статус, endpoint и безопасное действие отзыва.",
        actions: `<a class="btn btn-secondary" href="#/subscriptions">К подпискам</a>`,
      })}
      ${filterBar("vpn", { placeholder: "Поиск по UUID, подписке, имени", statusOptions: statusOptions(["all", "active", "provisioning", "revoked", "expired", "disabled"]) })}
      ${renderTable({
        route: "vpn",
        rows,
        empty: emptyState("VPN-конфигов пока нет", "После provisioning здесь появятся выданные ключи клиентов.", "⌘"),
        columns: [
          { label: "Конфиг", render: (row) => titleCell(row.display_name || `config #${row.vpn_configuration_id}`, `subscription #${row.subscription_id}`) },
          { label: "UUID", render: (row) => copyableText(row.client_uuid) },
          { label: "Endpoint", render: (row) => `#${escapeHtml(row.server_endpoint_id)}` },
          { label: "Статус", render: (row) => badge(row.status) },
          { label: "Создан", render: (row) => formatDateTime(row.created_at) },
          { label: "Действия", render: (row) => row.status === "active" || row.status === "provisioning" ? `<button class="btn btn-danger btn-sm" type="button" data-action="revokeVpn" data-id="${row.vpn_configuration_id}">Отозвать</button>` : `<span class="muted">Нет действий</span>` },
        ],
      })}
    `;
  }

  function renderTasks() {
    const rows = applySearchAndStatus("tasks", data("tasks"), (row) => [
      row.node_task_id,
      row.node_id,
      row.operation,
      row.status,
      row.last_error,
      row.idempotency_key,
    ].join(" "));
    return `
      ${pageHeader({
        title: "Очередь задач узлов",
        description: "Задачи provisioning/revoke показаны отдельно: администратор видит попытки, ошибки и retry.",
        actions: `<button class="btn btn-secondary" type="button" data-action="reconcile">Сверить подписки</button>`,
      })}
      ${filterBar("tasks", { placeholder: "Поиск по задаче, операции, ошибке", statusOptions: statusOptions(["all", "pending", "running", "completed", "failed", "retrying"]) })}
      ${renderTable({
        route: "tasks",
        rows,
        empty: emptyState("Очередь пуста", "Когда системе нужно выдать или отозвать доступ, задача появится здесь.", "↻"),
        columns: [
          { label: "Задача", render: (row) => titleCell(`#${row.node_task_id}`, `${row.operation || "operation"} · node #${row.node_id}`) },
          { label: "Статус", render: (row) => badge(row.status) },
          { label: "Попытки", render: (row) => `${row.attempts || 0}/${row.max_attempts || 0}` },
          { label: "Следующая попытка", render: (row) => formatDateTime(row.next_retry_at) },
          { label: "Ошибка", render: (row) => smallDetails(row.last_error || "нет") },
          { label: "Действия", render: (row) => ["pending", "failed", "retrying"].includes(String(row.status)) ? `<button class="btn btn-secondary btn-sm" type="button" data-action="dispatchTask" data-id="${row.node_task_id}">Отправить</button>` : `<span class="muted">Нет действий</span>` },
        ],
      })}
    `;
  }

  function renderSystem() {
    const envItems = [
      ["ADMIN_API_TOKEN", "Доступ к этой панели и admin API"],
      ["INTERNAL_API_KEY", "Безопасная связь Telegram-бота с backend"],
      ["BOT_TOKEN", "Telegram bot token"],
      ["PAYMENT_DEFAULT_PROVIDER", "dummy, yookassa, cryptobot, heleket или ton"],
      ["DEMO_NODE_AUTO_REGISTER", "Автоматическая регистрация demo node-agent"],
      ["NODE_REQUEST_TIMEOUT_SECONDS", "Timeout запросов к node-agent"],
    ];

    return `
      ${pageHeader({
        title: "Система и настройки",
        description: "В архиве нет API редактирования настроек, поэтому панель показывает безопасную операционную карту без изменения env-контрактов.",
        actions: `<a class="btn btn-secondary" href="/docs" target="_blank" rel="noreferrer">Открыть API docs</a>`,
      })}
      <section class="grid-2">
        <div class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Health checks</h2>
              <p class="card-subtitle">Состояние backend-зависимостей.</p>
            </div>
          </div>
          <div class="card-body kpi-list">
            ${kpiRow("/health/live", state.errors.live ? humanizeError(state.errors.live) : "OK", state.errors.live ? "danger" : "success")}
            ${kpiRow("/health/ready", state.errors.ready ? humanizeError(state.errors.ready) : "OK", state.errors.ready ? "warning" : "success")}
            ${kpiRow("Последнее обновление", state.lastLoadedAt ? formatDateTime(state.lastLoadedAt) : "нет данных", "neutral")}
          </div>
        </div>
        <div class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Карта env-настроек</h2>
              <p class="card-subtitle">Панель не переименовывает переменные и не меняет контракт .env.</p>
            </div>
          </div>
          <div class="card-body settings-list">
            ${envItems.map(([key, description]) => `
              <div class="settings-row">
                <div><strong><code>${key}</code></strong><span>${description}</span></div>
                ${badge("env", "neutral")}
              </div>
            `).join("")}
          </div>
        </div>
      </section>
      <section class="card mt-5">
        <div class="card-header">
          <div>
            <h2 class="card-title">Доступные admin API</h2>
            <p class="card-subtitle">Публичные контракты не изменены. Интерфейс использует их как есть.</p>
          </div>
        </div>
        <div class="card-body pill-row">
          ${DATASETS.filter(([, path]) => path.startsWith("/admin")).map(([, path]) => `<code>${escapeHtml(path)}</code>`).join("")}
        </div>
      </section>
    `;
  }

  function renderHelp() {
    return `
      ${pageHeader({
        title: "Справка администратора",
        description: "Короткие подсказки прямо в интерфейсе снижают необходимость помнить технические детали.",
        actions: `<a class="btn btn-primary" href="/docs" target="_blank" rel="noreferrer">Swagger /docs</a>`,
      })}
      <section class="grid-3">
        ${infoCard("Быстрый старт", "Создайте тариф, сервер и endpoint. Затем проверьте платежи и очередь задач.", "1")}
        ${infoCard("Multinode", "Добавьте node-agent, затем привяжите endpoint к node_id и local_inbound_id.", "2")}
        ${infoCard("Ошибки", "При сетевых ошибках используйте Обновить. При failed task откройте очередь и отправьте задачу повторно.", "3")}
      </section>
      <section class="card mt-5">
        <div class="card-header">
          <div>
            <h2 class="card-title">UX-логика панели</h2>
            <p class="card-subtitle">Что сделано для ежедневной работы администратора.</p>
          </div>
        </div>
        <div class="card-body settings-list">
          ${settingsRow("Одна главная CTA на странице", "Редкие действия вынесены в контекстные кнопки и подтверждения.")}
          ${settingsRow("Понятные статусы", "Все статусы отображаются бейджами с единой цветовой семантикой.")}
          ${settingsRow("Восстановление после ошибок", "Есть retry, человекочитаемые ошибки и сохранение введённых данных в форме до отправки.")}
          ${settingsRow("Адаптивность", "Sidebar превращается в мобильный drawer, таблицы получают контролируемый горизонтальный скролл.")}
        </div>
      </section>
    `;
  }

  function openTariffDialog() {
    openDialog({
      title: "Создать тариф",
      subtitle: "Базовые поля сверху. Описание и активность — ниже, чтобы не перегружать форму.",
      body: `
        <form id="tariff-form" class="stack" novalidate>
          ${field("tariff_name", "Название тарифа", "text", { placeholder: "1 месяц", required: true, help: "Показывается администратору и используется ботом." })}
          <div class="grid-2">
            ${field("price", "Цена", "number", { placeholder: "199", min: "0.01", step: "0.01", required: true, help: "В рублях или выбранной валюте." })}
            ${field("period_days", "Период, дней", "number", { placeholder: "30", min: "1", step: "1", required: true })}
          </div>
          <div class="grid-2">
            ${field("currency", "Валюта", "text", { value: "RUB", required: true, maxlength: "8" })}
            ${switchField("is_enabled", "Тариф включён", true, "Отключённый тариф останется в системе, но не должен продаваться.")}
          </div>
          ${textareaField("description", "Описание", { placeholder: "Доступ на 30 дней. Подходит для личного использования.", help: "Коротко объясните ценность тарифа." })}
        </form>
      `,
      footer: `
        <button class="btn btn-ghost" type="button" data-dialog-close>Отмена</button>
        <button class="btn btn-primary" type="submit" form="tariff-form">Создать тариф</button>
      `,
      onMount: (root) => {
        const form = $("#tariff-form", root);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const payload = getFormPayload(form);
          const errors = {};
          if (!payload.tariff_name) errors.tariff_name = "Укажите название тарифа.";
          if (!isPositiveNumber(payload.price)) errors.price = "Цена должна быть больше нуля.";
          if (!isPositiveInteger(payload.period_days)) errors.period_days = "Период должен быть целым числом больше нуля.";
          if (!payload.currency) errors.currency = "Укажите валюту.";
          if (showFormErrors(form, errors)) return;

          await submitDialogForm(form, async () => {
            await apiRequest("/admin/tariffs", {
              method: "POST",
              body: JSON.stringify({
                tariff_name: payload.tariff_name,
                price_minor: Math.round(Number(payload.price) * 100),
                currency: payload.currency.toUpperCase(),
                period_days: Number(payload.period_days),
                description: payload.description || null,
                is_enabled: Boolean(payload.is_enabled),
              }),
            });
            await refreshAfterMutation("Тариф создан", "Новый тариф доступен в списке.");
          });
        });
      },
    });
  }

  function openServerDialog() {
    openDialog({
      title: "Добавить сервер",
      subtitle: "Сервер хранит публичный host для VLESS-ссылок. API endpoint node-agent задаётся отдельно в разделе Узлы.",
      body: `
        <form id="server-form" class="stack" novalidate>
          ${field("server_name", "Название сервера", "text", { placeholder: "Germany 01", required: true })}
          ${field("host", "Публичный VLESS host", "text", { placeholder: "de1.example.com", required: true, help: "Не URL панели, а host, который попадёт в конфигурацию клиента." })}
          ${switchField("is_enabled", "Сервер включён", true, "Выключенный сервер не должен использоваться для новых конфигураций.")}
        </form>
      `,
      footer: `
        <button class="btn btn-ghost" type="button" data-dialog-close>Отмена</button>
        <button class="btn btn-primary" type="submit" form="server-form">Добавить сервер</button>
      `,
      onMount: (root) => {
        const form = $("#server-form", root);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const payload = getFormPayload(form);
          const errors = {};
          if (!payload.server_name) errors.server_name = "Укажите понятное название.";
          if (!payload.host) errors.host = "Укажите host для VLESS-конфигов.";
          if (showFormErrors(form, errors)) return;

          await submitDialogForm(form, async () => {
            await apiRequest("/admin/servers", {
              method: "POST",
              body: JSON.stringify({
                server_name: payload.server_name,
                host: payload.host,
                is_enabled: Boolean(payload.is_enabled),
              }),
            });
            await refreshAfterMutation("Сервер добавлен", "Теперь можно создать endpoint для этого сервера.");
          });
        });
      },
    });
  }

  function openEndpointDialog() {
    const servers = data("servers");
    const nodes = data("nodes");
    if (!servers.length) {
      showToast("warning", "Сначала добавьте сервер", "Endpoint должен быть привязан к существующему серверу.");
      return;
    }

    openDialog({
      title: "Добавить endpoint",
      subtitle: "Основные параметры видны сразу. Reality/transport детали спрятаны в расширенных настройках.",
      body: `
        <form id="endpoint-form" class="stack" novalidate>
          <div class="grid-2">
            ${selectField("server_id", "Сервер", servers.map((server) => [server.server_id, `${server.server_name} · ${server.host}`]), { required: true })}
            ${selectField("node_id", "Узел", [["", "Legacy panel / без node-agent"], ...nodes.map((node) => [node.node_id, `${node.display_name || node.node_key} · ${getNodeStatus(node)}`])], { help: "Опционально. Если задано, provisioning пойдёт через node-agent." })}
          </div>
          <div class="grid-3">
            ${field("protocol", "Protocol", "text", { value: "vless", required: true })}
            ${field("port", "Port", "number", { value: "443", min: "1", max: "65535", step: "1", required: true })}
            ${field("security", "Security", "text", { value: "reality", placeholder: "reality" })}
          </div>
          ${switchField("is_enabled", "Endpoint включён", true, "Отключите endpoint, если временно не хотите выдавать через него доступ.")}
          <details class="accordion">
            <summary>Расширенные параметры VLESS</summary>
            <div class="accordion-body">
              <div class="grid-2">
                ${field("local_inbound_id", "Local inbound ID", "text", { placeholder: "main-vless" })}
                ${field("sni", "SNI", "text", { placeholder: "example.com" })}
              </div>
              <div class="grid-2">
                ${field("fingerprint", "Fingerprint", "text", { placeholder: "chrome" })}
                ${field("public_key", "Public key", "text", { placeholder: "Reality public key" })}
              </div>
              <div class="grid-2">
                ${field("short_id", "Short ID", "text", { placeholder: "12345678" })}
                ${field("transport_type", "Transport", "text", { placeholder: "tcp" })}
              </div>
              <div class="grid-2">
                ${field("flow", "Flow", "text", { placeholder: "xtls-rprx-vision" })}
                ${field("encryption", "Encryption", "text", { placeholder: "none" })}
              </div>
            </div>
          </details>
        </form>
      `,
      footer: `
        <button class="btn btn-ghost" type="button" data-dialog-close>Отмена</button>
        <button class="btn btn-primary" type="submit" form="endpoint-form">Добавить endpoint</button>
      `,
      onMount: (root) => {
        const form = $("#endpoint-form", root);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const payload = getFormPayload(form);
          const errors = {};
          if (!payload.server_id) errors.server_id = "Выберите сервер.";
          if (!payload.protocol) errors.protocol = "Укажите protocol.";
          if (!isPort(payload.port)) errors.port = "Port должен быть числом от 1 до 65535.";
          if (showFormErrors(form, errors)) return;

          await submitDialogForm(form, async () => {
            await apiRequest("/admin/server-endpoints", {
              method: "POST",
              body: JSON.stringify({
                server_id: Number(payload.server_id),
                protocol: payload.protocol,
                port: Number(payload.port),
                node_id: payload.node_id ? Number(payload.node_id) : null,
                local_inbound_id: payload.local_inbound_id || null,
                security: payload.security || null,
                sni: payload.sni || null,
                fingerprint: payload.fingerprint || null,
                public_key: payload.public_key || null,
                short_id: payload.short_id || null,
                transport_type: payload.transport_type || null,
                flow: payload.flow || null,
                encryption: payload.encryption || null,
                is_enabled: Boolean(payload.is_enabled),
              }),
            });
            await refreshAfterMutation("Endpoint добавлен", "Новый endpoint доступен для provisioning.");
          });
        });
      },
    });
  }

  function openNodeDialog() {
    openDialog({
      title: "Добавить узел",
      subtitle: "Узел регистрирует node-agent и создаёт credential. Shared secret можно задать вручную или дать API сгенерировать его.",
      body: `
        <form id="node-form" class="stack" novalidate>
          <div class="grid-2">
            ${field("display_name", "Название", "text", { placeholder: "Germany node", required: true })}
            ${field("node_key", "Node key", "text", { placeholder: "de-node-01", required: true })}
          </div>
          ${field("api_base_url", "Node-agent API URL", "url", { placeholder: "https://node.example.com", required: true, help: "URL node-agent, не публичный VLESS host." })}
          <div class="grid-2">
            ${field("key_id", "Key ID", "text", { value: "default", required: true })}
            ${field("selection_weight", "Вес выбора", "number", { value: "100", min: "1", step: "1" })}
          </div>
          ${switchField("is_enabled", "Узел включён", true, "Выключенный узел не должен получать новые задачи.")}
          <details class="accordion">
            <summary>Расширенные параметры безопасности</summary>
            <div class="accordion-body">
              ${field("shared_secret", "Shared secret", "text", { placeholder: "Оставьте пустым для автогенерации", help: "Сохраните значение сразу после создания, если задаёте его вручную." })}
            </div>
          </details>
        </form>
      `,
      footer: `
        <button class="btn btn-ghost" type="button" data-dialog-close>Отмена</button>
        <button class="btn btn-primary" type="submit" form="node-form">Добавить узел</button>
      `,
      onMount: (root) => {
        const form = $("#node-form", root);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          const payload = getFormPayload(form);
          const errors = {};
          if (!payload.display_name) errors.display_name = "Укажите название.";
          if (!payload.node_key) errors.node_key = "Укажите node key.";
          if (!isValidUrl(payload.api_base_url)) errors.api_base_url = "Введите корректный URL node-agent.";
          if (!payload.key_id) errors.key_id = "Укажите key ID.";
          if (!isPositiveInteger(payload.selection_weight)) errors.selection_weight = "Вес должен быть целым числом больше нуля.";
          if (showFormErrors(form, errors)) return;

          await submitDialogForm(form, async () => {
            const response = await apiRequest("/admin/nodes", {
              method: "POST",
              body: JSON.stringify({
                display_name: payload.display_name,
                node_key: payload.node_key,
                api_base_url: payload.api_base_url,
                key_id: payload.key_id,
                shared_secret: payload.shared_secret || null,
                selection_weight: Number(payload.selection_weight),
                is_enabled: Boolean(payload.is_enabled),
              }),
            });
            closeDialog();
            await loadAllData();
            renderRoute();
            showNodeSecretDialog(response);
          }, { closeOnSuccess: false });
        });
      },
    });
  }

  function showNodeSecretDialog(response) {
    const credential = response?.credential || {};
    const secret = credential.shared_secret || "";
    openDialog({
      title: "Узел создан",
      subtitle: "Сохраните shared secret сейчас. Позже он может быть недоступен в интерфейсе.",
      body: `
        ${alertBox("info", "Credential готов", "Укажите key_id и shared_secret в настройках node-agent.")}
        <div class="settings-list mt-4">
          ${settingsRow("Node ID", `#${escapeHtml(response?.node?.node_id || "")}`)}
          ${settingsRow("Key ID", escapeHtml(credential.key_id || "default"))}
          ${settingsRow("Shared secret", secret ? `<code>${escapeHtml(secret)}</code>` : "secret не вернулся из API")}
        </div>
      `,
      footer: `
        ${secret ? `<button class="btn btn-secondary" type="button" data-action="copyText" data-copy="${escapeAttr(secret)}">Скопировать secret</button>` : ""}
        <button class="btn btn-primary" type="button" data-dialog-close>Готово</button>
      `,
    });
  }

  function markPaymentPaid(paymentOrderId) {
    confirmServerAction({
      title: `Отметить заказ #${paymentOrderId} оплаченным?`,
      message: "Это создаст payment event и поставит обработку в очередь. Используйте только если платёж действительно получен.",
      confirmLabel: "Отметить оплаченным",
      run: () => apiRequest(`/admin/payment-orders/${paymentOrderId}/mark-paid`, { method: "POST" }),
      success: "Платёж поставлен в обработку.",
    });
  }

  function provisionSubscription(subscriptionId) {
    confirmServerAction({
      title: `Выдать доступ по подписке #${subscriptionId}?`,
      message: "Система поставит provisioning в очередь. Если endpoint привязан к node-agent, задача появится в очереди узла.",
      confirmLabel: "Выдать доступ",
      run: () => apiRequest(`/admin/subscriptions/${subscriptionId}/provision`, { method: "POST" }),
      success: "Provisioning поставлен в очередь.",
    });
  }

  function revokeVpnConfiguration(vpnConfigurationId) {
    confirmServerAction({
      title: `Отозвать VPN-конфиг #${vpnConfigurationId}?`,
      message: "Это опасное действие: клиент может потерять доступ. Проверьте подписку и причину отзыва.",
      confirmLabel: "Отозвать конфиг",
      variant: "danger",
      run: () => apiRequest(`/admin/vpn-configurations/${vpnConfigurationId}/revoke`, { method: "POST" }),
      success: "Отзыв VPN-конфига поставлен в очередь.",
    });
  }

  function syncNode(nodeId) {
    confirmServerAction({
      title: `Синхронизировать узел #${nodeId}?`,
      message: "API запросит статус node-agent и обновит состояние узла.",
      confirmLabel: "Синхронизировать",
      run: () => apiRequest(`/admin/nodes/${nodeId}/sync`, { method: "POST" }),
      success: "Синхронизация узла выполнена.",
    });
  }

  function dispatchTask(nodeTaskId) {
    confirmServerAction({
      title: `Отправить задачу #${nodeTaskId}?`,
      message: "Задача будет принудительно отправлена в worker queue для dispatch на node-agent.",
      confirmLabel: "Отправить задачу",
      run: () => apiRequest(`/admin/nodes/tasks/${nodeTaskId}/dispatch`, { method: "POST" }),
      success: "Задача отправлена в очередь.",
    });
  }

  function confirmServerAction({ title, message, confirmLabel, run, success, variant = "primary" }) {
    openDialog({
      title,
      subtitle: "Подтвердите действие, чтобы избежать случайных операций.",
      body: alertBox(variant === "danger" ? "danger" : "warning", "Подтверждение", message),
      footer: `
        <button class="btn btn-ghost" type="button" data-dialog-close>Отмена</button>
        <button id="confirm-action" class="btn ${variant === "danger" ? "btn-danger" : "btn-primary"}" type="button">${escapeHtml(confirmLabel)}</button>
      `,
      onMount: (root) => {
        $("#confirm-action", root).addEventListener("click", async (event) => {
          const button = event.currentTarget;
          setButtonPending(button, true, "Выполняем...");
          try {
            await run();
            closeDialog();
            await loadAllData();
            renderRoute();
            showToast("success", "Готово", success);
          } catch (error) {
            showToast("danger", "Действие не выполнено", humanizeError(error));
            setButtonPending(button, false);
          }
        });
      },
    });
  }

  async function submitDialogForm(form, onSubmit, options = {}) {
    const submit = form.closest(".dialog").querySelector("button[type='submit']");
    setButtonPending(submit, true, "Сохраняем...");
    try {
      await onSubmit();
      if (options.closeOnSuccess !== false) {
        closeDialog();
      }
    } catch (error) {
      showToast("danger", "Не удалось сохранить", humanizeError(error));
      setButtonPending(submit, false);
    }
  }

  async function refreshAfterMutation(title, message) {
    closeDialog();
    await loadAllData();
    renderRoute();
    showToast("success", title, message);
  }

  function openDialog({ title, subtitle = "", body = "", footer = "", onMount = null }) {
    const root = $("#modal-root");
    root.innerHTML = `
      <section class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title" aria-describedby="dialog-subtitle">
        <header class="dialog-header">
          <div>
            <h2 id="dialog-title" class="dialog-title">${escapeHtml(title)}</h2>
            <p id="dialog-subtitle" class="dialog-subtitle">${escapeHtml(subtitle)}</p>
          </div>
          <button class="icon-button" type="button" data-dialog-close aria-label="Закрыть окно">×</button>
        </header>
        <div class="dialog-body">${body}</div>
        <footer class="dialog-footer">${footer}</footer>
      </section>
    `;
    document.body.classList.add("modal-open");
    $$('[data-dialog-close]', root).forEach((button) => button.addEventListener("click", closeDialog));
    if (onMount) onMount(root);
    setTimeout(() => root.querySelector("button, input, select, textarea")?.focus(), 0);
  }

  function closeDialog() {
    $("#modal-root").innerHTML = "";
    document.body.classList.remove("modal-open");
  }

  function renderTable({ route, rows, columns, empty }) {
    if (!rows.length) {
      return `<div class="card">${empty}</div>`;
    }
    const { pageRows, totalPages, page } = paginate(route, rows);
    return `
      <div class="card table-card">
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>${columns.map((column) => `<th scope="col">${escapeHtml(column.label)}</th>`).join("")}</tr>
            </thead>
            <tbody>
              ${pageRows.map((row) => `
                <tr>${columns.map((column) => `<td>${column.render(row)}</td>`).join("")}</tr>
              `).join("")}
            </tbody>
          </table>
        </div>
        ${totalPages > 1 ? renderPagination(route, page, totalPages) : ""}
      </div>
    `;
  }

  function paginate(route, rows, perPage = 10) {
    const totalPages = Math.max(1, Math.ceil(rows.length / perPage));
    const page = Math.min(Math.max(Number(state.pagination[route] || 1), 1), totalPages);
    const start = (page - 1) * perPage;
    return { pageRows: rows.slice(start, start + perPage), totalPages, page };
  }

  function renderPagination(route, page, totalPages) {
    return `
      <div class="pagination" aria-label="Пагинация">
        <button class="btn btn-secondary btn-sm" type="button" ${page === 1 ? "disabled" : ""} data-action="setPage" data-route-page="${route}" data-page="${page - 1}">Назад</button>
        <span class="muted">${page} из ${totalPages}</span>
        <button class="btn btn-secondary btn-sm" type="button" ${page === totalPages ? "disabled" : ""} data-action="setPage" data-route-page="${route}" data-page="${page + 1}">Вперёд</button>
      </div>
    `;
  }

  function filterBar(route, { placeholder, statusOptions: options }) {
    const searchValue = state.search[route] || "";
    const status = getFilter(route, "status", "all");
    return `
      <div class="filter-bar" role="search">
        <div class="filter-controls">
          <label class="field field-fluid">
            <span class="field-label">Поиск</span>
            <input class="input" type="search" data-search="${route}" value="${escapeAttr(searchValue)}" placeholder="${escapeAttr(placeholder)}" />
          </label>
          <label class="field">
            <span class="field-label">Статус</span>
            <select class="select" data-filter="${route}:status">
              ${options.map(([value, label]) => `<option value="${escapeAttr(value)}" ${status === value ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}
            </select>
          </label>
        </div>
      </div>
    `;
  }

  function statusOptions(keys) {
    const labels = {
      all: "Все статусы",
      active: "Активные",
      enabled: "Включены",
      disabled: "Выключены",
      ended: "Завершены",
      cancelled: "Отменены",
      paused: "На паузе",
      created: "Созданы",
      pending: "Ожидают",
      paid: "Оплачены",
      failed: "Ошибки",
      offline: "Offline",
      online: "Online",
      unknown: "Unknown",
      provisioning: "Provisioning",
      revoked: "Отозваны",
      expired: "Истекли",
      running: "В работе",
      completed: "Завершены",
      retrying: "Retrying",
    };
    return keys.map((key) => [key, labels[key] || key]);
  }

  function applySearchAndStatus(route, rows, toText) {
    const query = String(state.search[route] || "").trim().toLowerCase();
    const status = getFilter(route, "status", "all");
    return rows.filter((row) => {
      const text = toText(row).toLowerCase();
      const matchesQuery = !query || text.includes(query);
      const matchesStatus = status === "all" || text.includes(status) || row.status === status ||
        (status === "enabled" && row.is_enabled === true) ||
        (status === "disabled" && row.is_enabled === false) ||
        (status === "online" && getNodeStatus(row) === "online") ||
        (status === "offline" && getNodeStatus(row) === "offline") ||
        (status === "unknown" && getNodeStatus(row) === "unknown");
      return matchesQuery && matchesStatus;
    });
  }

  function tabs(route, active, items) {
    return `
      <div class="tabs" role="tablist">
        ${items.map(([key, label]) => `
          <button class="tab-button" role="tab" aria-selected="${active === key}" type="button" data-action="setTab" data-id="${key}">${escapeHtml(label)}</button>
        `).join("")}
      </div>
    `;
  }

  function pageHeader({ title, description, actions = "" }) {
    return `
      <header class="page-header">
        <div>
          <p class="eyebrow">VLESS ShopBot</p>
          <h2 class="page-title">${escapeHtml(title)}</h2>
          <p class="muted">${escapeHtml(description)}</p>
        </div>
        <div class="page-header-actions">${actions}</div>
      </header>
    `;
  }

  function statCard(label, value, note) {
    return `
      <article class="stat-card">
        <p class="stat-label">${escapeHtml(label)}</p>
        <p class="stat-value">${escapeHtml(value)}</p>
        <p class="stat-note">${escapeHtml(note)}</p>
      </article>
    `;
  }

  function infoCard(title, text, marker) {
    return `
      <article class="card">
        <div class="card-body stack-sm">
          <div class="empty-icon" aria-hidden="true">${escapeHtml(marker)}</div>
          <h3 class="card-title">${escapeHtml(title)}</h3>
          <p class="muted">${escapeHtml(text)}</p>
        </div>
      </article>
    `;
  }

  function titleCell(title, subtitle = "") {
    return `<span class="cell-title">${escapeHtml(title ?? "—")}</span>${subtitle ? `<span class="cell-subtitle">${escapeHtml(subtitle)}</span>` : ""}`;
  }

  function smallDetails(value) {
    return `<span class="cell-subtitle">${escapeHtml(value || "—")}</span>`;
  }

  function copyableText(value) {
    if (!value) return `<span class="muted">—</span>`;
    return `
      <span class="cell-subtitle">${escapeHtml(String(value).slice(0, 18))}...</span>
      <button class="btn btn-ghost btn-sm" type="button" data-action="copyText" data-copy="${escapeAttr(value)}">Копировать</button>
    `;
  }

  function kpiRow(label, value, tone = "neutral") {
    return `
      <div class="kpi-row">
        <span>${escapeHtml(label)}</span>
        <strong>${badge(value, tone)}</strong>
      </div>
    `;
  }

  function settingsRow(label, value) {
    return `
      <div class="settings-row">
        <div><strong>${escapeHtml(label)}</strong><span>${value}</span></div>
      </div>
    `;
  }

  function badge(value, tone = null) {
    const normalized = String(value ?? "unknown").toLowerCase();
    const badgeTone = tone || statusTone(normalized);
    return `<span class="badge badge-${badgeTone}">${escapeHtml(humanStatus(normalized))}</span>`;
  }

  function alertBox(type, title, message) {
    return `
      <div class="alert alert-${type}" role="status">
        <span class="status-dot status-${type === "danger" ? "danger" : type === "warning" ? "warning" : "info"}" aria-hidden="true"></span>
        <div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>
      </div>
    `;
  }

  function emptyState(title, message, icon = "∅", action = "") {
    return `
      <div class="empty-state">
        <div class="empty-icon" aria-hidden="true">${escapeHtml(icon)}</div>
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(message)}</p>
        ${action}
      </div>
    `;
  }

  function errorState(title, message) {
    return `
      <div class="card">
        <div class="error-state">
          <div class="error-icon" aria-hidden="true">!</div>
          <h3>${escapeHtml(title)}</h3>
          <p>${escapeHtml(message)}</p>
          <button class="btn btn-primary" type="button" data-action="retryLoad">Повторить</button>
        </div>
      </div>
    `;
  }

  function renderSkeletonPage() {
    return `
      <div class="skeleton-grid">
        <div class="skeleton-line skeleton-w-sm"></div>
        <div class="grid-4">
          <div class="skeleton-card"></div><div class="skeleton-card"></div><div class="skeleton-card"></div><div class="skeleton-card"></div>
        </div>
        <div class="skeleton-card skeleton-h-table"></div>
      </div>
    `;
  }

  function renderErrorSummary() {
    const keys = Object.keys(state.errors).filter((key) => key !== "ready" && key !== "live");
    if (!keys.length) return "";
    const message = keys.map((key) => `${key}: ${humanizeError(state.errors[key])}`).join(" ");
    return errorState("Часть данных не загрузилась", message);
  }

  function field(name, label, type = "text", options = {}) {
    return `
      <label class="field" data-field="${escapeAttr(name)}">
        <span class="field-label">${escapeHtml(label)}</span>
        <input
          class="input"
          id="${escapeAttr(name)}"
          name="${escapeAttr(name)}"
          type="${escapeAttr(type)}"
          value="${escapeAttr(options.value || "")}"
          placeholder="${escapeAttr(options.placeholder || "")}"
          ${options.required ? "required" : ""}
          ${options.min ? `min="${escapeAttr(options.min)}"` : ""}
          ${options.max ? `max="${escapeAttr(options.max)}"` : ""}
          ${options.step ? `step="${escapeAttr(options.step)}"` : ""}
          ${options.maxlength ? `maxlength="${escapeAttr(options.maxlength)}"` : ""}
        />
        ${options.help ? `<span class="field-help">${escapeHtml(options.help)}</span>` : ""}
        <span class="form-error" data-error-for="${escapeAttr(name)}"></span>
      </label>
    `;
  }

  function textareaField(name, label, options = {}) {
    return `
      <label class="field" data-field="${escapeAttr(name)}">
        <span class="field-label">${escapeHtml(label)}</span>
        <textarea class="textarea" id="${escapeAttr(name)}" name="${escapeAttr(name)}" placeholder="${escapeAttr(options.placeholder || "")}">${escapeHtml(options.value || "")}</textarea>
        ${options.help ? `<span class="field-help">${escapeHtml(options.help)}</span>` : ""}
        <span class="form-error" data-error-for="${escapeAttr(name)}"></span>
      </label>
    `;
  }

  function selectField(name, label, optionsList, options = {}) {
    return `
      <label class="field" data-field="${escapeAttr(name)}">
        <span class="field-label">${escapeHtml(label)}</span>
        <select class="select" id="${escapeAttr(name)}" name="${escapeAttr(name)}" ${options.required ? "required" : ""}>
          ${optionsList.map(([value, optionLabel]) => `<option value="${escapeAttr(value)}">${escapeHtml(optionLabel)}</option>`).join("")}
        </select>
        ${options.help ? `<span class="field-help">${escapeHtml(options.help)}</span>` : ""}
        <span class="form-error" data-error-for="${escapeAttr(name)}"></span>
      </label>
    `;
  }

  function switchField(name, label, checked = false, help = "") {
    return `
      <label class="switch-row" data-field="${escapeAttr(name)}">
        <span><strong>${escapeHtml(label)}</strong>${help ? `<span class="cell-subtitle">${escapeHtml(help)}</span>` : ""}</span>
        <input type="checkbox" name="${escapeAttr(name)}" ${checked ? "checked" : ""} />
      </label>
    `;
  }

  function getFormPayload(form) {
    const payload = {};
    const formData = new FormData(form);
    for (const [key, value] of formData.entries()) {
      payload[key] = String(value).trim();
    }
    $$('input[type="checkbox"]', form).forEach((input) => {
      payload[input.name] = input.checked;
    });
    return payload;
  }

  function showFormErrors(form, errors) {
    $$(".field, .switch-row", form).forEach((fieldNode) => fieldNode.classList.remove("has-error"));
    $$(".form-error", form).forEach((errorNode) => {
      errorNode.textContent = "";
    });
    const names = Object.keys(errors);
    names.forEach((name) => {
      const fieldNode = form.querySelector(`[data-field="${CSS.escape(name)}"]`);
      const errorNode = form.querySelector(`[data-error-for="${CSS.escape(name)}"]`);
      fieldNode?.classList.add("has-error");
      if (errorNode) errorNode.textContent = errors[name];
    });
    if (names.length) {
      form.querySelector(`[name="${CSS.escape(names[0])}"]`)?.focus();
      return true;
    }
    return false;
  }

  function setButtonPending(button, isPending, label = "Сохраняем...") {
    if (!button) return;
    if (isPending) {
      button.dataset.originalLabel = button.textContent;
      button.textContent = label;
      button.disabled = true;
    } else {
      button.textContent = button.dataset.originalLabel || button.textContent;
      button.disabled = false;
      delete button.dataset.originalLabel;
    }
  }

  function showToast(type, title, message) {
    const root = $("#toast-root");
    const id = `toast-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.id = id;
    toast.innerHTML = `
      <span class="status-dot status-${type === "danger" ? "danger" : type === "success" ? "success" : type === "warning" ? "warning" : "info"}" aria-hidden="true"></span>
      <div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>
    `;
    root.appendChild(toast);
    setTimeout(() => toast.remove(), 5200);
  }

  function humanizeError(error) {
    if (!error) return "Неизвестная ошибка.";
    if (error.status === 0) return "Нет связи с API. Проверьте, что backend запущен и доступен из браузера.";
    if (error.status === 401) return "Административный токен не принят. Проверьте ADMIN_API_TOKEN.";
    if (error.status === 404) return "Сущность не найдена. Обновите данные и попробуйте ещё раз.";
    if (error.status === 409) return "Конфликт данных: такая сущность уже существует или нарушено уникальное ограничение.";
    if (error.status === 422) return "Проверьте поля формы: API отклонил некорректные данные.";
    if (error.status >= 500) return "Backend не смог выполнить запрос. Проверьте логи API/worker и повторите действие.";
    return error.message || "Запрос не выполнен.";
  }

  function humanStatus(value) {
    const map = {
      active: "Активно",
      enabled: "Включено",
      disabled: "Выключено",
      ended: "Завершено",
      cancelled: "Отменено",
      paused: "Пауза",
      created: "Создано",
      pending: "Ожидает",
      paid: "Оплачено",
      succeeded: "Успешно",
      completed: "Завершено",
      failed: "Ошибка",
      retrying: "Повтор",
      running: "В работе",
      revoked: "Отозвано",
      expired: "Истекло",
      provisioning: "Выдаётся",
      online: "Online",
      offline: "Offline",
      unknown: "Unknown",
      ready: "Ready",
      attention: "Внимание",
      dummy: "Dummy",
      yookassa: "YooKassa",
      cryptobot: "CryptoBot",
      heleket: "Heleket",
      ton: "TON",
      env: "ENV",
    };
    return map[String(value).toLowerCase()] || String(value || "—");
  }

  function statusTone(value) {
    const normalized = String(value || "").toLowerCase();
    if (["active", "enabled", "paid", "succeeded", "completed", "online", "ready", "ok"].includes(normalized)) return "success";
    if (["pending", "created", "retrying", "provisioning", "running", "attention"].includes(normalized)) return "warning";
    if (["failed", "revoked", "expired", "disabled", "offline", "cancelled", "error"].includes(normalized)) return "danger";
    if (["dummy", "yookassa", "cryptobot", "heleket", "ton", "env"].includes(normalized)) return "info";
    return "neutral";
  }

  function isPaidOrder(order) {
    return String(order.status || "").toLowerCase() === "paid" || Boolean(order.paid_at);
  }

  function getNodeStatus(node) {
    return String(node.health_status || node.status || "unknown").toLowerCase();
  }

  function formatMoney(minor, currency = "RUB") {
    const value = Number(minor || 0) / 100;
    try {
      return new Intl.NumberFormat("ru-RU", { style: "currency", currency: currency || "RUB" }).format(value);
    } catch {
      return `${value.toFixed(2)} ${currency || ""}`.trim();
    }
  }

  function formatDateTime(value) {
    if (!value) return "—";
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function latestDate(current, ...values) {
    const dates = [current, ...values]
      .filter(Boolean)
      .map((value) => new Date(value))
      .filter((date) => !Number.isNaN(date.getTime()))
      .sort((a, b) => b.getTime() - a.getTime());
    return dates[0]?.toISOString() || current;
  }

  function compareDateDesc(a, b) {
    return new Date(b || 0).getTime() - new Date(a || 0).getTime();
  }

  function isPositiveNumber(value) {
    return Number(value) > 0;
  }

  function isPositiveInteger(value) {
    const number = Number(value);
    return Number.isInteger(number) && number > 0;
  }

  function isPort(value) {
    const number = Number(value);
    return Number.isInteger(number) && number >= 1 && number <= 65535;
  }

  function isValidUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol);
    } catch {
      return false;
    }
  }

  async function copyToClipboard(value) {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      showToast("success", "Скопировано", "Значение помещено в буфер обмена.");
    } catch {
      showToast("warning", "Не удалось скопировать", "Скопируйте значение вручную из поля.");
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }
})();
