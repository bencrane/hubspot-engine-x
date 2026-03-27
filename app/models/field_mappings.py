"""Pydantic models for field mapping CRUD endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class SetFieldMappingRequest(BaseModel):
    client_id: UUID
    canonical_object: str
    canonical_field: str
    hubspot_object: str
    hubspot_property: str


class GetFieldMappingsRequest(BaseModel):
    client_id: UUID
    canonical_object: str


class ListFieldMappingsRequest(BaseModel):
    client_id: UUID


class DeleteFieldMappingRequest(BaseModel):
    client_id: UUID
    mapping_id: UUID


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class FieldMappingResponse(BaseModel):
    id: UUID
    org_id: UUID
    client_id: UUID
    canonical_object: str
    canonical_field: str
    hubspot_object: str
    hubspot_property: str
    transform_rule: dict[str, Any] | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FieldMappingListResponse(BaseModel):
    mappings: list[FieldMappingResponse]


class DeleteFieldMappingResponse(BaseModel):
    id: UUID
    deleted: bool
