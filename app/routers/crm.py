"""CRM read endpoints — search, list, get, batch-read, associations, pipelines, lists."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.context import AuthContext
from app.auth.dependencies import require_permission, validate_client_access
from app.db import get_db, get_pool
from app.models.crm import (
    AssociationRecord,
    AssociationRequest,
    AssociationResponse,
    BatchAssociationRequest,
    BatchAssociationResponse,
    CrmBatchReadRequest,
    CrmBatchReadResponse,
    CrmGetRequest,
    CrmGetResponse,
    CrmListRequest,
    CrmListResponse,
    CrmRecord,
    CrmSearchRequest,
    CrmSearchResponse,
    HubSpotList,
    HubSpotListResponse,
    ListMembersRequest,
    ListMembersResponse,
    ListsRequest,
    Pipeline,
    PipelineRequest,
    PipelineResponse,
    PipelineStage,
)
from app.services.hubspot import (
    HubSpotAPIError,
    HubSpotClient,
    resolve_connection,
)

router = APIRouter(prefix="/api/crm", tags=["crm"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hubspot_as_bad_gateway(exc: HubSpotAPIError) -> HTTPException:
    detail = f"HubSpot API error: {exc.message}"
    if exc.correlation_id:
        detail += f" (correlationId: {exc.correlation_id})"
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


def _to_crm_record(raw: dict) -> CrmRecord:
    return CrmRecord(
        id=str(raw.get("id", "")),
        properties=raw.get("properties", {}),
        created_at=raw.get("createdAt"),
        updated_at=raw.get("updatedAt"),
    )


# ---------------------------------------------------------------------------
# CRM Object Endpoints
# ---------------------------------------------------------------------------


@router.post("/search", response_model=CrmSearchResponse)
async def search_records(
    request: CrmSearchRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> CrmSearchResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)
    try:
        data = await hs.search_objects(
            request.object_type,
            filter_groups=request.filter_groups,
            properties=request.properties,
            sorts=request.sorts,
            limit=request.limit,
            after=request.after,
        )
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    next_after = data.get("paging", {}).get("next", {}).get("after")
    return CrmSearchResponse(
        total=data.get("total", 0),
        results=[_to_crm_record(r) for r in data.get("results", [])],
        next_after=next_after,
    )


@router.post("/list", response_model=CrmListResponse)
async def list_records(
    request: CrmListRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> CrmListResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)
    try:
        data = await hs.list_objects(
            request.object_type,
            properties=request.properties,
            limit=request.limit,
            after=request.after,
        )
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    next_after = data.get("paging", {}).get("next", {}).get("after")
    return CrmListResponse(
        results=[_to_crm_record(r) for r in data.get("results", [])],
        next_after=next_after,
    )


@router.post("/get", response_model=CrmGetResponse)
async def get_record(
    request: CrmGetRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> CrmGetResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)
    try:
        data = await hs.get_object(request.object_type, request.object_id)
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    return CrmGetResponse(record=_to_crm_record(data))


@router.post("/batch-read", response_model=CrmBatchReadResponse)
async def batch_read_records(
    request: CrmBatchReadRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> CrmBatchReadResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)
    try:
        data = await hs.batch_read(
            request.object_type, request.ids, properties=request.properties
        )
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    return CrmBatchReadResponse(
        results=[_to_crm_record(r) for r in data.get("results", [])]
    )


# ---------------------------------------------------------------------------
# Association Endpoints
# ---------------------------------------------------------------------------


def _to_association_records(raw_results: list[dict]) -> list[AssociationRecord]:
    records: list[AssociationRecord] = []
    for item in raw_results:
        from_id = str(item.get("from", {}).get("id", item.get("id", "")))
        for assoc in item.get("to", []):
            records.append(
                AssociationRecord(
                    from_id=from_id,
                    to_id=str(assoc.get("id", "")),
                    association_types=assoc.get("associationTypes", []),
                )
            )
    return records


@router.post("/associations", response_model=AssociationResponse)
async def get_associations(
    request: AssociationRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> AssociationResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)
    try:
        data = await hs.list_associations(
            request.from_object_type,
            request.to_object_type,
            request.object_id,
        )
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    results = data.get("results", [])
    assoc_records = [
        AssociationRecord(
            from_id=request.object_id,
            to_id=str(r.get("id", "")),
            association_types=r.get("associationTypes", r.get("type", [])),
        )
        for r in results
    ]
    next_after = data.get("paging", {}).get("next", {}).get("after")
    return AssociationResponse(results=assoc_records, next_after=next_after)


@router.post("/associations/batch", response_model=BatchAssociationResponse)
async def batch_read_associations(
    request: BatchAssociationRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> BatchAssociationResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)
    try:
        data = await hs.batch_read_associations(
            request.from_object_type,
            request.to_object_type,
            request.object_ids,
        )
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    return BatchAssociationResponse(
        results=_to_association_records(data.get("results", []))
    )


# ---------------------------------------------------------------------------
# Pipeline Endpoints
# ---------------------------------------------------------------------------


@router.post("/pipelines", response_model=PipelineResponse)
async def list_pipelines(
    request: PipelineRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> PipelineResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)
    try:
        raw_pipelines = await hs.list_pipelines(request.object_type)
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    pipelines: list[Pipeline] = []
    for p in raw_pipelines:
        stages = [
            PipelineStage(
                stage_id=str(s.get("stageId", s.get("id", ""))),
                label=str(s.get("label", "")),
                display_order=int(s.get("displayOrder", 0)),
            )
            for s in p.get("stages", [])
        ]
        pipelines.append(
            Pipeline(
                pipeline_id=str(p.get("pipelineId", p.get("id", ""))),
                label=str(p.get("label", "")),
                stages=stages,
            )
        )

    return PipelineResponse(pipelines=pipelines)


# ---------------------------------------------------------------------------
# HubSpot Lists Endpoints
# ---------------------------------------------------------------------------


@router.post("/lists", response_model=HubSpotListResponse)
async def list_hubspot_lists(
    request: ListsRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> HubSpotListResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)
    try:
        data = await hs.list_lists(limit=request.limit, after=request.after)
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    lists = [
        HubSpotList(
            list_id=str(item.get("listId", item.get("id", ""))),
            name=str(item.get("name", "")),
            size=item.get("size"),
            list_type=item.get("listType"),
        )
        for item in data.get("lists", data.get("results", []))
    ]
    next_after = data.get("paging", {}).get("next", {}).get("after")
    return HubSpotListResponse(lists=lists, next_after=next_after)


@router.post("/lists/members", response_model=ListMembersResponse)
async def get_list_members(
    request: ListMembersRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> ListMembersResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)
    try:
        data = await hs.get_list_memberships(
            request.list_id, limit=request.limit, after=request.after
        )
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    record_ids = [str(r) for r in data.get("results", [])]
    next_after = data.get("paging", {}).get("next", {}).get("after")
    return ListMembersResponse(record_ids=record_ids, next_after=next_after)
