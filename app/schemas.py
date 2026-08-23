from __future__ import annotations
from pydantic import BaseModel
from typing import Any


class EntityWrapper(BaseModel):
    entity: dict[str, Any]


class WebhookPayloadInner(BaseModel):
    subscription: EntityWrapper
    payment: EntityWrapper


class WebhookPayload(BaseModel):
    event: str
    payload: WebhookPayloadInner
