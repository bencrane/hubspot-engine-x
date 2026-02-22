import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.context import AuthContext
from app.auth.dependencies import require_permission, validate_client_access
from app.db import get_db, get_pool
from app.models.connections import (
    CallbackConnectionRequest,
    ConnectionResponse,
    ConnectionSessionResponse,
    CreateConnectionRequest,
    GetConnectionRequest,
    ListConnectionsRequest,
    ListConnectionsResponse,
    RefreshConnectionRequest,
    RefreshConnectionResponse,
    RevokeConnectionRequest,
)
from app.services import token_manager
from app.services.token_manager import NangoAPIError

router = APIRouter(prefix="/api/connections", tags=["connections"])


def _nango_as_bad_gateway(exc: NangoAPIError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"External service error: {exc.message}",
    )


def _normalize_scopes(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value)
    return str(value)


def _extract_connection_metadata(connection_data: dict) -> tuple[str | None, str | None, str | None]:
    connection_config = connection_data.get("connection_config", {}) or {}
    credentials = connection_data.get("credentials", {}) or {}
    raw_credentials = credentials.get("raw", {}) or {}

    portal_id = connection_config.get("portalId") or raw_credentials.get("hub_id")
    hub_domain = raw_credentials.get("hub_domain")
    scopes = raw_credentials.get("scope") or raw_credentials.get("scopes")

    return (
        str(hub_domain) if hub_domain is not None else None,
        str(portal_id) if portal_id is not None else None,
        _normalize_scopes(scopes),
    )


@router.post("/create", response_model=ConnectionSessionResponse)
async def create_connection(
    request: CreateConnectionRequest,
    auth: AuthContext = Depends(require_permission("connections.write")),
    db: asyncpg.Connection = Depends(get_db),
) -> ConnectionSessionResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())

    org_id = uuid.UUID(auth.org_id)
    existing = await db.fetchrow(
        """
        SELECT id, status
        FROM crm_connections
        WHERE client_id = $1 AND org_id = $2
        """,
        request.client_id,
        org_id,
    )

    if existing is not None and existing["status"] == "connected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connection already active for this client",
        )

    try:
        session_data = await token_manager.create_connect_session(str(request.client_id), auth.org_id)
    except NangoAPIError as exc:
        raise _nango_as_bad_gateway(exc) from None

    nango_connection_id = str(request.client_id)
    if existing is None:
        await db.fetchrow(
            """
            INSERT INTO crm_connections (org_id, client_id, nango_connection_id, status)
            VALUES ($1, $2, $3, 'pending')
            RETURNING id, org_id, client_id, nango_connection_id, status, hub_domain, hubspot_portal_id,
                      scopes, last_used_at, error_message, created_at, updated_at
            """,
            org_id,
            request.client_id,
            nango_connection_id,
        )
    else:
        await db.fetchrow(
            """
            UPDATE crm_connections
            SET status = 'pending',
                error_message = NULL,
                nango_connection_id = $3
            WHERE client_id = $1 AND org_id = $2
            RETURNING id, org_id, client_id, nango_connection_id, status, hub_domain, hubspot_portal_id,
                      scopes, last_used_at, error_message, created_at, updated_at
            """,
            request.client_id,
            org_id,
            nango_connection_id,
        )

    return ConnectionSessionResponse(
        session_token=str(session_data.get("token", "")),
        connect_link=str(session_data.get("connect_link", "")),
        expires_at=str(session_data.get("expires_at", "")),
    )


@router.post("/callback", response_model=ConnectionResponse)
async def callback_connection(
    request: CallbackConnectionRequest,
    auth: AuthContext = Depends(require_permission("connections.write")),
    db: asyncpg.Connection = Depends(get_db),
) -> ConnectionResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())

    org_id = uuid.UUID(auth.org_id)
    existing = await db.fetchrow(
        """
        SELECT id, status
        FROM crm_connections
        WHERE client_id = $1 AND org_id = $2
        """,
        request.client_id,
        org_id,
    )
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    try:
        connection_data = await token_manager.get_connection(str(request.client_id))
    except NangoAPIError as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth flow not completed — connection not found in provider",
            ) from None
        raise _nango_as_bad_gateway(exc) from None

    hub_domain, hubspot_portal_id, scopes = _extract_connection_metadata(connection_data)

    row = await db.fetchrow(
        """
        UPDATE crm_connections
        SET status = 'connected',
            hub_domain = $3,
            hubspot_portal_id = $4,
            scopes = $5,
            nango_connection_id = $6,
            error_message = NULL
        WHERE client_id = $1 AND org_id = $2
        RETURNING id, org_id, client_id, nango_connection_id, status, hub_domain, hubspot_portal_id,
                  scopes, last_used_at, error_message, created_at, updated_at
        """,
        request.client_id,
        org_id,
        hub_domain,
        hubspot_portal_id,
        scopes,
        str(request.client_id),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update connection")

    return ConnectionResponse(**dict(row))


@router.post("/list", response_model=ListConnectionsResponse)
async def list_connections(
    request: ListConnectionsRequest,
    auth: AuthContext = Depends(require_permission("connections.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> ListConnectionsResponse:
    org_id = uuid.UUID(auth.org_id)

    effective_client_id: uuid.UUID | None = None
    request_client_filter_applied = False

    if auth.client_id is not None:
        effective_client_id = uuid.UUID(auth.client_id)
    elif request.client_id is not None:
        effective_client_id = request.client_id
        request_client_filter_applied = True

    if request_client_filter_applied and request.client_id is not None:
        await validate_client_access(auth, str(request.client_id), get_pool())

    query = """
        SELECT id, org_id, client_id, nango_connection_id, status, hub_domain, hubspot_portal_id,
               scopes, last_used_at, error_message, created_at, updated_at
        FROM crm_connections
        WHERE org_id = $1
    """
    params: list[object] = [org_id]
    next_param = 2

    if effective_client_id is not None:
        query += f" AND client_id = ${next_param}"
        params.append(effective_client_id)
        next_param += 1

    if request.status is not None:
        query += f" AND status = ${next_param}"
        params.append(request.status)

    query += " ORDER BY created_at DESC"
    rows = await db.fetch(query, *params)

    return ListConnectionsResponse(connections=[ConnectionResponse(**dict(row)) for row in rows])


@router.post("/get", response_model=ConnectionResponse)
async def get_connection(
    request: GetConnectionRequest,
    auth: AuthContext = Depends(require_permission("connections.read")),
    db: asyncpg.Connection = Depends(get_db),
) -> ConnectionResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())

    org_id = uuid.UUID(auth.org_id)
    row = await db.fetchrow(
        """
        SELECT id, org_id, client_id, nango_connection_id, status, hub_domain, hubspot_portal_id,
               scopes, last_used_at, error_message, created_at, updated_at
        FROM crm_connections
        WHERE client_id = $1 AND org_id = $2
        """,
        request.client_id,
        org_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    return ConnectionResponse(**dict(row))


@router.post("/refresh", response_model=RefreshConnectionResponse)
async def refresh_connection(
    request: RefreshConnectionRequest,
    auth: AuthContext = Depends(require_permission("connections.write")),
    db: asyncpg.Connection = Depends(get_db),
) -> RefreshConnectionResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())

    org_id = uuid.UUID(auth.org_id)
    existing = await db.fetchrow(
        """
        SELECT id
        FROM crm_connections
        WHERE client_id = $1 AND org_id = $2 AND status = 'connected'
        """,
        request.client_id,
        org_id,
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active connection not found for this client",
        )

    try:
        _ = await token_manager.get_valid_token(str(request.client_id))
    except NangoAPIError as exc:
        if exc.status_code == 424:
            await db.execute(
                """
                UPDATE crm_connections
                SET status = 'expired', error_message = $3
                WHERE client_id = $1 AND org_id = $2
                """,
                request.client_id,
                org_id,
                exc.message,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Connection expired — HubSpot token refresh failed",
            ) from None
        raise _nango_as_bad_gateway(exc) from None

    await db.execute(
        """
        UPDATE crm_connections
        SET last_used_at = NOW()
        WHERE client_id = $1 AND org_id = $2
        """,
        request.client_id,
        org_id,
    )

    return RefreshConnectionResponse(status="refreshed", client_id=request.client_id)


@router.post("/revoke", response_model=ConnectionResponse)
async def revoke_connection(
    request: RevokeConnectionRequest,
    auth: AuthContext = Depends(require_permission("connections.write")),
    db: asyncpg.Connection = Depends(get_db),
) -> ConnectionResponse:
    await validate_client_access(auth, str(request.client_id), get_pool())

    org_id = uuid.UUID(auth.org_id)
    existing = await db.fetchrow(
        """
        SELECT id, status
        FROM crm_connections
        WHERE client_id = $1 AND org_id = $2
        """,
        request.client_id,
        org_id,
    )
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    if existing["status"] == "revoked":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connection already revoked",
        )

    try:
        await token_manager.delete_connection(str(request.client_id))
    except NangoAPIError as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise _nango_as_bad_gateway(exc) from None

    row = await db.fetchrow(
        """
        UPDATE crm_connections
        SET status = 'revoked', error_message = NULL
        WHERE client_id = $1 AND org_id = $2
        RETURNING id, org_id, client_id, nango_connection_id, status, hub_domain, hubspot_portal_id,
                  scopes, last_used_at, error_message, created_at, updated_at
        """,
        request.client_id,
        org_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revoke connection")

    return ConnectionResponse(**dict(row))
