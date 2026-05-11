$AppJsPath = "C:\Users\Admin\Desktop\vless-shopbot-reworked-git\src\shop_bot\apps\api\static\admin\assets\app.js"

if (!(Test-Path $AppJsPath)) {
    throw "app.js not found: $AppJsPath"
}

$BackupPath = "$AppJsPath.encoding-bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $AppJsPath $BackupPath -Force
Write-Host "Backup created:"
Write-Host $BackupPath

$content = Get-Content $AppJsPath -Raw -Encoding UTF8

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
        title: "\u0422\u0430\u0440\u0438\u0444\u044b",
        description: "\u041f\u0440\u043e\u0431\u043d\u044b\u0439 \u043f\u0435\u0440\u0438\u043e\u0434 \u0438 \u043f\u043b\u0430\u0442\u043d\u044b\u0435 \u0442\u0430\u0440\u0438\u0444\u044b. \u041e\u0442\u043a\u043b\u044e\u0447\u0451\u043d\u043d\u044b\u0435 \u0442\u0430\u0440\u0438\u0444\u044b \u043e\u0441\u0442\u0430\u044e\u0442\u0441\u044f \u0432 \u0438\u0441\u0442\u043e\u0440\u0438\u0438, \u043d\u043e \u043d\u0435 \u043f\u0440\u043e\u0434\u0430\u044e\u0442\u0441\u044f \u0432 \u0431\u043e\u0442\u0435.",
        actions: `<button class="btn btn-primary" type="button" data-action="createTariff">\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0442\u0430\u0440\u0438\u0444</button>`,
      })}
      ${filterBar("plans", { placeholder: "\u041f\u043e\u0438\u0441\u043a \u043f\u043e \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u044e, \u0446\u0435\u043d\u0435, \u0432\u0430\u043b\u044e\u0442\u0435", statusOptions: statusOptions(["all", "enabled", "disabled"]) })}
      ${renderTable({
        route: "plans",
        rows,
        empty: emptyState("\u041f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0442\u0430\u0440\u0438\u0444\u043e\u0432", "\u0421\u043e\u0437\u0434\u0430\u0439\u0442\u0435 \u043f\u0435\u0440\u0432\u044b\u0439 \u0442\u0430\u0440\u0438\u0444, \u0447\u0442\u043e\u0431\u044b \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438 \u043c\u043e\u0433\u043b\u0438 \u043e\u0444\u043e\u0440\u043c\u0438\u0442\u044c \u043f\u043e\u0434\u043f\u0438\u0441\u043a\u0443.", "\u20bd", `<button class="btn btn-primary" type="button" data-action="createTariff">\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0442\u0430\u0440\u0438\u0444</button>`),
        columns: [
          { label: "\u0422\u0430\u0440\u0438\u0444", render: (row) => titleCell(row.tariff_name, row.description || "\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043d\u0435 \u0437\u0430\u0434\u0430\u043d\u043e") },
          { label: "\u0421\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c", render: (row) => titleCell(formatMoney(row.price_minor, row.currency), `${row.period_days} \u0434\u043d.`) },
          { label: "\u0421\u0442\u0430\u0442\u0443\u0441", render: (row) => badge(row.is_enabled ? "enabled" : "disabled") },
          { label: "ID", render: (row) => `#${escapeHtml(row.tariff_id)}` },
          {
            label: "\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u044f",
            render: (row) => `
              <div class="actions-inline">
                <button class="btn btn-secondary btn-sm" type="button" data-action="editTariff" data-id="${escapeAttr(row.tariff_id)}">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c</button>
                ${
                  row.is_enabled
                    ? `<button class="btn btn-danger btn-sm" type="button" data-action="disableTariff" data-id="${escapeAttr(row.tariff_id)}">\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c</button>`
                    : `<button class="btn btn-secondary btn-sm" type="button" data-action="enableTariff" data-id="${escapeAttr(row.tariff_id)}">\u0412\u043a\u043b\u044e\u0447\u0438\u0442\u044c</button>`
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

$openTariffDialog = @'
  function openTariffDialog(tariff = null) {
    const isEdit = Boolean(tariff);
    const title = isEdit ? `\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0442\u0430\u0440\u0438\u0444 #${tariff.tariff_id}` : "\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0442\u0430\u0440\u0438\u0444";
    const submitLabel = isEdit ? "\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0442\u0430\u0440\u0438\u0444" : "\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0442\u0430\u0440\u0438\u0444";

    openDialog({
      title,
      subtitle: "\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u0441\u0440\u0430\u0437\u0443 \u043f\u043e\u043f\u0430\u0434\u0443\u0442 \u0432 API. \u0415\u0441\u043b\u0438 \u0442\u0430\u0440\u0438\u0444 \u043e\u0442\u043a\u043b\u044e\u0447\u0451\u043d, \u0431\u043e\u0442 \u043d\u0435 \u0431\u0443\u0434\u0435\u0442 \u043f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0442\u044c \u0435\u0433\u043e \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f\u043c.",
      body: `
        <form id="tariff-form" class="stack" novalidate>
          ${field("tariff_name", "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0442\u0430\u0440\u0438\u0444\u0430", "text", {
            value: tariff?.tariff_name || "",
            placeholder: "\u041f\u0440\u043e\u0431\u043d\u044b\u0439 \u043f\u0435\u0440\u0438\u043e\u0434 \u2014 3 \u0434\u043d\u044f",
            required: true,
            help: "\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442\u0441\u044f \u0432 \u0430\u0434\u043c\u0438\u043d\u043a\u0435 \u0438 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0435\u0442\u0441\u044f \u0431\u043e\u0442\u043e\u043c.",
          })}
          <div class="grid-2">
            ${field("price", "\u0426\u0435\u043d\u0430", "number", {
              value: tariff ? String(Number(tariff.price_minor || 0) / 100) : "",
              placeholder: "1",
              min: "0.01",
              step: "0.01",
              required: true,
              help: "\u0412 \u0440\u0443\u0431\u043b\u044f\u0445 \u0438\u043b\u0438 \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u043e\u0439 \u0432\u0430\u043b\u044e\u0442\u0435.",
            })}
            ${field("period_days", "\u041f\u0435\u0440\u0438\u043e\u0434, \u0434\u043d\u0435\u0439", "number", {
              value: tariff?.period_days || "",
              placeholder: "3",
              min: "1",
              step: "1",
              required: true,
            })}
          </div>
          <div class="grid-2">
            ${field("currency", "\u0412\u0430\u043b\u044e\u0442\u0430", "text", {
              value: tariff?.currency || "RUB",
              required: true,
              maxlength: "8",
            })}
            ${switchField("is_enabled", "\u0422\u0430\u0440\u0438\u0444 \u0432\u043a\u043b\u044e\u0447\u0451\u043d", tariff ? Boolean(tariff.is_enabled) : true, "\u041e\u0442\u043a\u043b\u044e\u0447\u0451\u043d\u043d\u044b\u0439 \u0442\u0430\u0440\u0438\u0444 \u043e\u0441\u0442\u0430\u0451\u0442\u0441\u044f \u0432 \u0441\u0438\u0441\u0442\u0435\u043c\u0435, \u043d\u043e \u043d\u0435 \u043f\u0440\u043e\u0434\u0430\u0451\u0442\u0441\u044f \u0432 \u0431\u043e\u0442\u0435.")}
          </div>
          ${textareaField("description", "\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435", {
            value: tariff?.description || "",
            placeholder: "\u0414\u043e\u0441\u0442\u0443\u043f \u043d\u0430 3 \u0434\u043d\u044f. \u041f\u043e\u0434\u0445\u043e\u0434\u0438\u0442 \u0434\u043b\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 VPN.",
            help: "\u041a\u043e\u0440\u043e\u0442\u043a\u043e \u043e\u0431\u044a\u044f\u0441\u043d\u0438\u0442\u0435 \u0446\u0435\u043d\u043d\u043e\u0441\u0442\u044c \u0442\u0430\u0440\u0438\u0444\u0430.",
          })}
        </form>
      `,
      footer: `
        <button class="btn btn-ghost" type="button" data-dialog-close>\u041e\u0442\u043c\u0435\u043d\u0430</button>
        <button class="btn btn-primary" type="submit" form="tariff-form">${submitLabel}</button>
      `,
      onMount: (root) => {
        const form = $("#tariff-form", root);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();

          const payload = getFormPayload(form);
          const errors = {};

          if (!payload.tariff_name) errors.tariff_name = "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0442\u0430\u0440\u0438\u0444\u0430.";
          if (!isPositiveNumber(payload.price)) errors.price = "\u0426\u0435\u043d\u0430 \u0434\u043e\u043b\u0436\u043d\u0430 \u0431\u044b\u0442\u044c \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0443\u043b\u044f.";
          if (!isPositiveInteger(payload.period_days)) errors.period_days = "\u041f\u0435\u0440\u0438\u043e\u0434 \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c \u0446\u0435\u043b\u044b\u043c \u0447\u0438\u0441\u043b\u043e\u043c \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0443\u043b\u044f.";
          if (!payload.currency) errors.currency = "\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u0432\u0430\u043b\u044e\u0442\u0443.";

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
              await refreshAfterMutation("\u0422\u0430\u0440\u0438\u0444 \u043e\u0431\u043d\u043e\u0432\u043b\u0451\u043d", "\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b.");
            } else {
              await apiRequest("/admin/tariffs", {
                method: "POST",
                body: JSON.stringify(requestBody),
              });
              await refreshAfterMutation("\u0422\u0430\u0440\u0438\u0444 \u0441\u043e\u0437\u0434\u0430\u043d", "\u041d\u043e\u0432\u044b\u0439 \u0442\u0430\u0440\u0438\u0444 \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d \u0432 \u0441\u043f\u0438\u0441\u043a\u0435.");
            }
          });
        });
      },
    });
  }

'@

$content = [regex]::Replace(
    $content,
    '(?s)  function openTariffDialog\(tariff = null\) \{.*?\n  function openServerDialog\(\) \{',
    $openTariffDialog + '  function openServerDialog() {'
)

$content = [regex]::Replace(
    $content,
    '(?s)  function openTariffDialog\(\) \{.*?\n  function openServerDialog\(\) \{',
    $openTariffDialog + '  function openServerDialog() {'
)

$setTariffEnabled = @'
  function setTariffEnabled(tariffId, isEnabled) {
    confirmServerAction({
      title: isEnabled ? `\u0412\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0442\u0430\u0440\u0438\u0444 #${tariffId}?` : `\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0442\u0430\u0440\u0438\u0444 #${tariffId}?`,
      message: isEnabled
        ? "\u0422\u0430\u0440\u0438\u0444 \u0441\u043d\u043e\u0432\u0430 \u043f\u043e\u044f\u0432\u0438\u0442\u0441\u044f \u0432 \u0431\u043e\u0442\u0435 \u0438 \u0441\u043c\u043e\u0436\u0435\u0442 \u043f\u0440\u043e\u0434\u0430\u0432\u0430\u0442\u044c\u0441\u044f \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f\u043c."
        : "\u0422\u0430\u0440\u0438\u0444 \u043e\u0441\u0442\u0430\u043d\u0435\u0442\u0441\u044f \u0432 \u0438\u0441\u0442\u043e\u0440\u0438\u0438 \u043f\u043b\u0430\u0442\u0435\u0436\u0435\u0439 \u0438 \u043f\u043e\u0434\u043f\u0438\u0441\u043e\u043a, \u043d\u043e \u0438\u0441\u0447\u0435\u0437\u043d\u0435\u0442 \u0438\u0437 \u0441\u043f\u0438\u0441\u043a\u0430 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0445 \u0442\u0430\u0440\u0438\u0444\u043e\u0432 \u0432 \u0431\u043e\u0442\u0435.",
      confirmLabel: isEnabled ? "\u0412\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0442\u0430\u0440\u0438\u0444" : "\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0442\u0430\u0440\u0438\u0444",
      variant: isEnabled ? "primary" : "danger",
      run: () => isEnabled
        ? apiRequest(`/admin/tariffs/${tariffId}/enable`, { method: "POST" })
        : apiRequest(`/admin/tariffs/${tariffId}`, { method: "DELETE" }),
      success: isEnabled ? "\u0422\u0430\u0440\u0438\u0444 \u0432\u043a\u043b\u044e\u0447\u0451\u043d." : "\u0422\u0430\u0440\u0438\u0444 \u043e\u0442\u043a\u043b\u044e\u0447\u0451\u043d.",
    });
  }

'@

$content = [regex]::Replace(
    $content,
    '(?s)  function setTariffEnabled\(tariffId, isEnabled\) \{.*?\n  function markPaymentPaid\(paymentOrderId\) \{',
    $setTariffEnabled + '  function markPaymentPaid(paymentOrderId) {'
)

Set-Content $AppJsPath $content -Encoding UTF8

Write-Host "Encoding-safe admin UI patch applied."