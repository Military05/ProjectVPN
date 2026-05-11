$AppJsPath = "C:\Users\Admin\Desktop\vless-shopbot-reworked-git\src\shop_bot\apps\api\static\admin\assets\app.js"

if (!(Test-Path $AppJsPath)) {
    throw "app.js not found: $AppJsPath"
}

$BackupPath = "$AppJsPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $AppJsPath $BackupPath -Force
Write-Host "Backup created:"
Write-Host $BackupPath

$content = Get-Content $AppJsPath -Raw -Encoding UTF8

# 1. Actions in handleMainClick
$content = $content.Replace(
'createTariff: openTariffDialog,',
'createTariff: () => openTariffDialog(),
      editTariff: () => openTariffDialog(findById("tariffs", "tariff_id", id)),
      disableTariff: () => setTariffEnabled(Number(id), false),
      enableTariff: () => setTariffEnabled(Number(id), true),'
)

# 2. Replace renderPlans()
$renderPlans = @'
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
        description: "Пробный период и платные тарифы. Отключённые тарифы остаются в истории, но не продаются в боте.",
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
          {
            label: "Действия",
            render: (row) => `
              <div class="actions-inline">
                <button class="btn btn-secondary btn-sm" type="button" data-action="editTariff" data-id="${escapeAttr(row.tariff_id)}">Редактировать</button>
                ${
                  row.is_enabled
                    ? `<button class="btn btn-danger btn-sm" type="button" data-action="disableTariff" data-id="${escapeAttr(row.tariff_id)}">Отключить</button>`
                    : `<button class="btn btn-secondary btn-sm" type="button" data-action="enableTariff" data-id="${escapeAttr(row.tariff_id)}">Включить</button>`
                }
              </div>
            `,
          },
        ],
      })}
    `;
  }

'@

$content = [regex]::Replace(
    $content,
    '(?s)  function renderPlans\(\) \{.*?\n  function renderServers\(\) \{',
    $renderPlans + '  function renderServers() {'
)

# 3. Replace openTariffDialog()
$openTariffDialog = @'
  function openTariffDialog(tariff = null) {
    const isEdit = Boolean(tariff);
    const title = isEdit ? `Редактировать тариф #${tariff.tariff_id}` : "Создать тариф";
    const submitLabel = isEdit ? "Сохранить тариф" : "Создать тариф";

    openDialog({
      title,
      subtitle: "Изменения сразу попадут в API. Если тариф отключён, бот не будет показывать его пользователям.",
      body: `
        <form id="tariff-form" class="stack" novalidate>
          ${field("tariff_name", "Название тарифа", "text", {
            value: tariff?.tariff_name || "",
            placeholder: "Пробный период — 3 дня",
            required: true,
            help: "Показывается в админке и используется ботом.",
          })}
          <div class="grid-2">
            ${field("price", "Цена", "number", {
              value: tariff ? String(Number(tariff.price_minor || 0) / 100) : "",
              placeholder: "1",
              min: "0.01",
              step: "0.01",
              required: true,
              help: "В рублях или выбранной валюте.",
            })}
            ${field("period_days", "Период, дней", "number", {
              value: tariff?.period_days || "",
              placeholder: "3",
              min: "1",
              step: "1",
              required: true,
            })}
          </div>
          <div class="grid-2">
            ${field("currency", "Валюта", "text", {
              value: tariff?.currency || "RUB",
              required: true,
              maxlength: "8",
            })}
            ${switchField("is_enabled", "Тариф включён", tariff ? Boolean(tariff.is_enabled) : true, "Отключённый тариф остаётся в системе, но не продаётся в боте.")}
          </div>
          ${textareaField("description", "Описание", {
            value: tariff?.description || "",
            placeholder: "Доступ на 3 дня. Подходит для проверки VPN.",
            help: "Коротко объясните ценность тарифа.",
          })}
        </form>
      `,
      footer: `
        <button class="btn btn-ghost" type="button" data-dialog-close>Отмена</button>
        <button class="btn btn-primary" type="submit" form="tariff-form">${submitLabel}</button>
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

          const requestBody = {
            tariff_name: payload.tariff_name,
            price_minor: Math.round(Number(payload.price) * 100),
            currency: payload.currency.toUpperCase(),
            period_days: Number(payload.period_days),
            description: payload.description || null,
            is_enabled: Boolean(payload.is_enabled),
          };

          await submitDialogForm(form, async () => {
            if (isEdit) {
              await apiRequest(`/admin/tariffs/${tariff.tariff_id}`, {
                method: "PATCH",
                body: JSON.stringify(requestBody),
              });
              await refreshAfterMutation("Тариф обновлён", "Изменения сохранены.");
            } else {
              await apiRequest("/admin/tariffs", {
                method: "POST",
                body: JSON.stringify(requestBody),
              });
              await refreshAfterMutation("Тариф создан", "Новый тариф доступен в списке.");
            }
          });
        });
      },
    });
  }

'@

$content = [regex]::Replace(
    $content,
    '(?s)  function openTariffDialog\(\) \{.*?\n  function openServerDialog\(\) \{',
    $openTariffDialog + '  function openServerDialog() {'
)

# 4. Insert setTariffEnabled()
$setTariffEnabled = @'
  function setTariffEnabled(tariffId, isEnabled) {
    confirmServerAction({
      title: isEnabled ? `Включить тариф #${tariffId}?` : `Отключить тариф #${tariffId}?`,
      message: isEnabled
        ? "Тариф снова появится в боте и сможет продаваться пользователям."
        : "Тариф останется в истории платежей и подписок, но исчезнет из списка доступных тарифов в боте.",
      confirmLabel: isEnabled ? "Включить тариф" : "Отключить тариф",
      variant: isEnabled ? "primary" : "danger",
      run: () => isEnabled
        ? apiRequest(`/admin/tariffs/${tariffId}/enable`, { method: "POST" })
        : apiRequest(`/admin/tariffs/${tariffId}`, { method: "DELETE" }),
      success: isEnabled ? "Тариф включён." : "Тариф отключён.",
    });
  }

'@

if ($content -notmatch 'function setTariffEnabled\(') {
    $content = $content.Replace('  function markPaymentPaid(paymentOrderId) {', $setTariffEnabled + '  function markPaymentPaid(paymentOrderId) {')
}

# 5. Insert findById()
$findById = @'

  function findById(datasetKey, idKey, idValue) {
    return data(datasetKey).find((item) => String(item[idKey]) === String(idValue)) || null;
  }
'@

if ($content -notmatch 'function findById\(') {
    $content = $content.Replace(
'  function data(key) {
    const value = state.data[key];
    return Array.isArray(value) ? value : [];
  }',
'  function data(key) {
    const value = state.data[key];
    return Array.isArray(value) ? value : [];
  }' + $findById
    )
}

Set-Content $AppJsPath $content -Encoding UTF8

Write-Host "app.js patched successfully."