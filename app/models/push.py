"""Pydantic models for push endpoints."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class PushRecordsRequest(BaseModel):
    client_id: UUID
    object_type: str
    records: list[dict] = Field(..., max_length=1000)
    id_property: str | None = None
    idempotency_key: str | None = None


class PushUpdateItem(BaseModel):
    id: str
    properties: dict[str, str | None]


class PushUpdateRequest(BaseModel):
    client_id: UUID
    object_type: str
    updates: list[PushUpdateItem] = Field(..., max_length=1000)
    idempotency_key: str | None = None


class AssociationInput(BaseModel):
    from_id: str
    to_id: str
    association_type: str | None = None


class PushLinkRequest(BaseModel):
    client_id: UUID
    from_object_type: str
    to_object_type: str
    associations: list[AssociationInput] = Field(..., max_length=1000)
    idempotency_key: str | None = None


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class PushResponse(BaseModel):
    push_log_id: UUID
    total: int
    succeeded: int
    failed: int
    errors: list[dict]
    warnings: list[str]
