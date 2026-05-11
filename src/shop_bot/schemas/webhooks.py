from __future__ import annotations

from pydantic import BaseModel


class WebhookAcceptedResponse(BaseModel):
    duplicate: bool
    event_key: str
    payment_event_id: int
