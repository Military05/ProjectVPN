from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse

from shop_bot.apps.api.deps import get_container
from shop_bot.application.commands.ingest_webhook_event import ingest_webhook_event
from shop_bot.bootstrap.container import ServiceContainer

router = APIRouter(tags=["sandbox"])


def _escape(value: object) -> str:
    return escape(str(value), quote=True)


def _payment_shell(title: str, body: str) -> str:
    safe_title = _escape(title)
    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{safe_title}</title>
        <style>
          :root {{
            color-scheme: light;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
              "Segoe UI", sans-serif;
            --bg: #f5f7fb;
            --surface: #ffffff;
            --text: #0f172a;
            --muted: #64748b;
            --border: #dde5f0;
            --primary: #2563eb;
            --primary-strong: #1d4ed8;
            --success: #15803d;
          }}
          * {{ box-sizing: border-box; }}
          body {{
            min-height: 100vh;
            margin: 0;
            display: grid;
            place-items: center;
            background:
              radial-gradient(circle at top left, rgba(37, 99, 235, 0.1), transparent 32rem),
              var(--bg);
            color: var(--text);
          }}
          main {{
            width: min(100% - 32px, 520px);
            border: 1px solid var(--border);
            border-radius: 24px;
            background: var(--surface);
            box-shadow: 0 18px 46px rgba(15, 23, 42, 0.12);
            padding: 32px;
          }}
          h1 {{ margin: 0 0 8px; letter-spacing: -0.04em; }}
          p {{ color: var(--muted); }}
          .meta {{
            display: grid;
            gap: 10px;
            margin: 24px 0;
          }}
          .row {{
            display: flex;
            justify-content: space-between;
            gap: 16px;
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 12px 14px;
          }}
          button {{
            width: 100%;
            min-height: 46px;
            border: 0;
            border-radius: 14px;
            background: var(--primary);
            color: white;
            font: inherit;
            font-weight: 800;
            cursor: pointer;
          }}
          button:hover {{ background: var(--primary-strong); }}
          .success {{ color: var(--success); font-weight: 800; }}
        </style>
      </head>
      <body>
        <main>{body}</main>
      </body>
    </html>
    """


@router.get("/sandbox/payments/{payment_order_id}", response_class=HTMLResponse)
async def dummy_payment_page(
    payment_order_id: int,
    container: ServiceContainer = Depends(get_container),
) -> HTMLResponse:
    async with container.uow() as uow:
        order = await uow.payments.get_order(payment_order_id)
        attempt = await uow.payments.get_latest_attempt(payment_order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if order["provider"] != "dummy":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sandbox is only available for dummy provider",
        )
    provider_payment_id = attempt["provider_payment_id"] if attempt is not None else ""
    amount = f"{order['amount_minor']} {order['currency']}"
    body = f"""
      <p class="success">Dummy payment sandbox</p>
      <h1>Confirm test payment</h1>
      <p>This page simulates a successful payment event for local development.</p>
      <div class="meta">
        <div class="row"><span>Order</span><strong>#{payment_order_id}</strong></div>
        <div class="row">
          <span>Amount</span>
          <strong>{_escape(amount)}</strong>
        </div>
        <div class="row"><span>Status</span><strong>{_escape(order['status'])}</strong></div>
      </div>
      <form method="post" action="/sandbox/payments/{payment_order_id}/pay">
        <input type="hidden" name="provider_payment_id" value="{_escape(provider_payment_id)}" />
        <button type="submit">Mark as paid</button>
      </form>
    """
    return HTMLResponse(content=_payment_shell("Dummy payment sandbox", body))


@router.post("/sandbox/payments/{payment_order_id}/pay", response_class=HTMLResponse)
async def pay_dummy_order(
    payment_order_id: int,
    provider_payment_id: str = Form(default=""),
    container: ServiceContainer = Depends(get_container),
) -> HTMLResponse:
    async with container.uow() as uow:
        order = await uow.payments.get_order(payment_order_id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
        if order["provider"] != "dummy":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sandbox is only available for dummy provider",
            )
        attempt = await uow.payments.get_latest_attempt(payment_order_id)
    provider_payment_id = provider_payment_id or (
        attempt["provider_payment_id"] if attempt is not None else ""
    )
    await ingest_webhook_event(
        container,
        provider="dummy",
        payload={
            "payment_order_id": payment_order_id,
            "provider_payment_id": provider_payment_id,
            "status": "paid",
            "amount_minor": int(order["amount_minor"]),
            "currency": str(order["currency"]),
        },
        headers={},
    )
    body = """
      <p class="success">Payment accepted</p>
      <h1>Payment event queued</h1>
      <p>The worker will finalize subscription activation and VPN provisioning.</p>
      <p>Return to Telegram and use <strong>/status</strong>.</p>
    """
    return HTMLResponse(content=_payment_shell("Payment accepted", body))
