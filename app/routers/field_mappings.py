"""Field mapping CRUD — canonical-to-HubSpot property mapping per client."""

from __future__ import annotations

import json
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.context import AuthContext
from app.auth.dependencies import require_permission, validate_client_access
from app.db import get_db, get_pool
from app.models.field_mappings import (
    DeleteFieldMappingRequest,
    DeleteFieldMappingResponse,
    FieldMappingListResponse,
    FieldMappingResponse,
    GetFieldMappingsRequest,
    ListFieldMappingsRequest,
    SetFieldMappingRequest,
)

router = APIRouter(prefix="/api/field-mappings", tags=["field-mappings"])


def _row_to_response(row: asyncpg.Record) -> FieldMappingResponse:
    data = dict(row)
    transform_rule = data.get("transform_rule")
    if isinstance(transform_rule, str):
        transform_rule = json.loads(transform_rule)
    data["transform_rule"] = transform_rule
    return FieldMappingResponse(**data)


@router.post("/set", response_model=FieldMappingResponse)
async def set_field_mapping(
    request: SetFieldMappingRequest,
    auth: AuthContext = Depends(require_permission("deploy.write")),
    db: asyncpg.Connection = Depends(get_db),
) -> FieldMappingResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())

    org_id = uuid.UUID(auth.org_id)
    transform_json = json.dumps(request.transform_rule) if request.transform_rule is not None else None

    row = await db.fetchrow(
        """
        INSERT INTO crm_field_mappings
            (org_id, client_id, canonical_object, canonical_field,
             hubspot_object, hubspot_property, transform_rule)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        ON CONFLICT (org_id, client_id, canonical_object, canonical_field)
        DO UPDATE SET
            hubspot_object = EXCLUDED.hubspot_object,
            hubspot_property = EXCLUDED.hubspot_property,
            transform_rule = EXCLUDED.transform_rule,
            is_active = TRUE
        RETURNING id, org_id, client_id, canonical_object, canonical_field,
                  hubspot_object, hubspot_property, transform_rule,
                  is_active, created_at, updated_at
        """,
        org_id,
        request.client_id,
        request.canonical_object,
        request.canonical_field,
        request.hubspot_object,
        request.hubspot_property,
        transform_json,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upsert field mapping",
        )

    return _row_to_response(row)


@router.post("/get", response_model=FieldMappingListResponse)
async def get_field_mappings(
    request: GetFieldMappingsRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> FieldMappingListResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())

    org_id = uuid.UUID(auth.org_id)
    rows = await db.fetch(
        """
        SELECT id, org_id, client_id, canonical_object, canonical_field,
               hubspot_object, hubspot_property, transform_rule,
               is_active, created_at, updated_at
        FROM crm_field_mappings
        WHERE org_id = $1 AND client_id = $2 AND canonical_object = $3
              AND is_active = TRUE
        ORDER BY canonical_field
        """,
        org_id,
        request.client_id,
        request.canonical_object,
    )

    return FieldMappingListResponse(mappings=[_row_to_response(r) for r in rows])


@router.post("/list", response_model=FieldMappingListResponse)
async def list_field_mappings(
    request: ListFieldMappingsRequest,
    auth: AuthContext = Depends(require_permission("topology.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> FieldMappingListResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())

    org_id = uuid.UUID(auth.org_id)
    rows = await db.fetch(
        """
        SELECT id, org_id, client_id, canonical_object, canonical_field,
               hubspot_object, hubspot_property, transform_rule,
               is_active, created_at, updated_at
        FROM crm_field_mappings
        WHERE org_id = $1 AND client_id = $2 AND is_active = TRUE
        ORDER BY canonical_object, canonical_field
        """,
        org_id,
        request.client_id,
    )

    return FieldMappingListResponse(mappings=[_row_to_response(r) for r in rows])


@router.post("/delete", response_model=DeleteFieldMappingResponse)
async def delete_field_mapping(
    request: DeleteFieldMappingRequest,
    auth: AuthContext = Depends(require_permission("deploy.write")),
    db: asyncpg.Connection = Depends(get_db),
) -> DeleteFieldMappingResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())

    org_id = uuid.UUID(auth.org_id)
    result = await db.execute(
        """
        UPDATE crm_field_mappings
        SET is_active = FALSE
        WHERE id = $1 AND org_id = $2 AND client_id = $3
        """,
        request.mapping_id,
        org_id,
        request.client_id,
    )

    if result == "UPDATE 0":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Field mapping not found",
        )

    return DeleteFieldMappingResponse(id=request.mapping_id, deleted=True)
