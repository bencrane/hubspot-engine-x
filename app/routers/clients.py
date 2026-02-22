import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.context import AuthContext
from app.auth.dependencies import require_permission
from app.db import get_db
from app.models.clients import (
    ClientResponse,
    CreateClientRequest,
    GetClientRequest,
    ListClientsRequest,
    ListClientsResponse,
)

router = APIRouter(prefix="/api/clients", tags=["clients"])


@router.post("/create", response_model=ClientResponse)
async def create_client(
    request: CreateClientRequest,
    auth: AuthContext = Depends(require_permission("org.manage")),
    db: asyncpg.Connection = Depends(get_db),
) -> ClientResponse:
    org_id = uuid.UUID(auth.org_id)
    try:
        row = await db.fetchrow(
            """
            INSERT INTO clients (org_id, name, domain)
            VALUES ($1, $2, $3)
            RETURNING id, org_id, name, domain, is_active, created_at, updated_at
            """,
            org_id,
            request.name,
            request.domain,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Client with this domain already exists in this organization",
        ) from None

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create client",
        )

    return ClientResponse(**dict(row))


@router.post("/list", response_model=ListClientsResponse)
async def list_clients(
    request: ListClientsRequest,
    auth: AuthContext = Depends(require_permission("org.manage")),
    db: asyncpg.Connection = Depends(get_db),
) -> ListClientsResponse:
    org_id = uuid.UUID(auth.org_id)
    query = """
        SELECT id, org_id, name, domain, is_active, created_at, updated_at
        FROM clients
        WHERE org_id = $1
    """
    params: list[object] = [org_id]

    if request.is_active is not None:
        query += " AND is_active = $2"
        params.append(request.is_active)

    query += " ORDER BY created_at DESC"
    rows = await db.fetch(query, *params)

    return ListClientsResponse(clients=[ClientResponse(**dict(row)) for row in rows])


@router.post("/get", response_model=ClientResponse)
async def get_client(
    request: GetClientRequest,
    auth: AuthContext = Depends(require_permission("org.manage")),
    db: asyncpg.Connection = Depends(get_db),
) -> ClientResponse:
    org_id = uuid.UUID(auth.org_id)
    row = await db.fetchrow(
        """
        SELECT id, org_id, name, domain, is_active, created_at, updated_at
        FROM clients
        WHERE id = $1 AND org_id = $2
        """,
        request.client_id,
        org_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    return ClientResponse(**dict(row))
