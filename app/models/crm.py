"""Pydantic models for CRM read endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared record model
# ---------------------------------------------------------------------------


class CrmRecord(BaseModel):
    id: str
    properties: dict[str, str | None]
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# CRM Search
# ---------------------------------------------------------------------------


class CrmSearchRequest(BaseModel):
    client_id: UUID
    object_type: str
    filter_groups: list[dict] | None = None
    properties: list[str] | None = None
    sorts: list[dict] | None = None
    limit: int = Field(default=100, ge=1, le=100)
    after: str | None = None


class CrmSearchResponse(BaseModel):
    total: int
    results: list[CrmRecord]
    next_after: str | None = None


# ---------------------------------------------------------------------------
# CRM List
# ---------------------------------------------------------------------------


class CrmListRequest(BaseModel):
    client_id: UUID
    object_type: str
    properties: list[str] | None = None
    limit: int = Field(default=100, ge=1, le=100)
    after: str | None = None


class CrmListResponse(BaseModel):
    results: list[CrmRecord]
    next_after: str | None = None


# ---------------------------------------------------------------------------
# CRM Get
# ---------------------------------------------------------------------------


class CrmGetRequest(BaseModel):
    client_id: UUID
    object_type: str
    object_id: str


class CrmGetResponse(BaseModel):
    record: CrmRecord


# ---------------------------------------------------------------------------
# CRM Batch Read
# ---------------------------------------------------------------------------


class CrmBatchReadRequest(BaseModel):
    client_id: UUID
    object_type: str
    ids: list[str] = Field(..., max_length=100)
    properties: list[str] | None = None


class CrmBatchReadResponse(BaseModel):
    results: list[CrmRecord]


# ---------------------------------------------------------------------------
# Associations
# ---------------------------------------------------------------------------


class AssociationRequest(BaseModel):
    client_id: UUID
    from_object_type: str
    to_object_type: str
    object_id: str


class BatchAssociationRequest(BaseModel):
    client_id: UUID
    from_object_type: str
    to_object_type: str
    object_ids: list[str] = Field(..., max_length=100)


class AssociationRecord(BaseModel):
    from_id: str
    to_id: str
    association_types: list[dict]


class AssociationResponse(BaseModel):
    results: list[AssociationRecord]
    next_after: str | None = None


class BatchAssociationResponse(BaseModel):
    results: list[AssociationRecord]


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


class PipelineRequest(BaseModel):
    client_id: UUID
    object_type: str


class PipelineStage(BaseModel):
    stage_id: str
    label: str
    display_order: int


class Pipeline(BaseModel):
    pipeline_id: str
    label: str
    stages: list[PipelineStage]


class PipelineResponse(BaseModel):
    pipelines: list[Pipeline]


# ---------------------------------------------------------------------------
# HubSpot Lists
# ---------------------------------------------------------------------------


class ListsRequest(BaseModel):
    client_id: UUID
    limit: int = Field(default=100, ge=1, le=250)
    after: str | None = None


class HubSpotList(BaseModel):
    list_id: str
    name: str
    size: int | None = None
    list_type: str | None = None


class HubSpotListResponse(BaseModel):
    lists: list[HubSpotList]
    next_after: str | None = None


class ListMembersRequest(BaseModel):
    client_id: UUID
    list_id: str
    limit: int = Field(default=100, ge=1, le=100)
    after: str | None = None


class ListMembersResponse(BaseModel):
    record_ids: list[str]
    next_after: str | None = None
