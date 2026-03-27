"""Push endpoints — batch upsert, update, and association creation."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.context import AuthContext
from app.auth.dependencies import require_permission, validate_client_access
from app.db import get_db, get_pool
from app.models.push import (
    PushLinkRequest,
    PushRecordsRequest,
    PushResponse,
    PushUpdateRequest,
)
from app.services.hubspot import (
    HubSpotAPIError,
    HubSpotClient,
    resolve_connection,
)
from app.services import push_service

router = APIRouter(prefix="/api/push", tags=["push"])


def _hubspot_as_bad_gateway(exc: HubSpotAPIError) -> HTTPException:
    detail = f"HubSpot API error: {exc.message}"
    if exc.correlation_id:
        detail += f" (correlationId: {exc.correlation_id})"
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


@router.post("/records", response_model=PushResponse)
async def push_records(
    request: PushRecordsRequest,
    auth: AuthContext = Depends(require_permission("push.write")),
    db: asyncpg.Connection = Depends(get_db),
) -> PushResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)

    try:
        result = await push_service.batch_upsert(
            hs,
            db,
            org_id=auth.org_id,
            client_id=str(request.client_id),
            connection_id=connection_id,
            pushed_by=auth.user_id,
            object_type=request.object_type,
            records=request.records,
            id_property=request.id_property,
            idempotency_key=request.idempotency_key,
        )
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    return PushResponse(
        push_log_id=result.push_log_id,
        total=result.total,
        succeeded=result.succeeded,
        failed=result.failed,
        errors=result.errors,
        warnings=result.warnings,
    )


@router.post("/update", response_model=PushResponse)
async def push_update(
    request: PushUpdateRequest,
    auth: AuthContext = Depends(require_permission("push.write")),
    db: asyncpg.Connection = Depends(get_db),
) -> PushResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)

    updates = [{"id": u.id, "properties": u.properties} for u in request.updates]

    try:
        result = await push_service.batch_update(
            hs,
            db,
            org_id=auth.org_id,
            client_id=str(request.client_id),
            connection_id=connection_id,
            pushed_by=auth.user_id,
            object_type=request.object_type,
            updates=updates,
            idempotency_key=request.idempotency_key,
        )
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    return PushResponse(
        push_log_id=result.push_log_id,
        total=result.total,
        succeeded=result.succeeded,
        failed=result.failed,
        errors=result.errors,
        warnings=result.warnings,
    )


@router.post("/link", response_model=PushResponse)
async def push_link(
    request: PushLinkRequest,
    auth: AuthContext = Depends(require_permission("push.write")),
    db: asyncpg.Connection = Depends(get_db),
) -> PushResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())
    connection_id = await resolve_connection(auth, request.client_id, db)
    hs = HubSpotClient(connection_id)

    associations = [
        {
            "from_id": a.from_id,
            "to_id": a.to_id,
            "association_type": a.association_type,
        }
        for a in request.associations
    ]

    try:
        result = await push_service.create_associations(
            hs,
            db,
            org_id=auth.org_id,
            client_id=str(request.client_id),
            connection_id=connection_id,
            pushed_by=auth.user_id,
            from_object_type=request.from_object_type,
            to_object_type=request.to_object_type,
            associations=associations,
            idempotency_key=request.idempotency_key,
        )
    except HubSpotAPIError as exc:
        raise _hubspot_as_bad_gateway(exc) from None

    return PushResponse(
        push_log_id=result.push_log_id,
        total=result.total,
        succeeded=result.succeeded,
        failed=result.failed,
        errors=result.errors,
        warnings=result.warnings,
    )
