import hashlib
import secrets
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.context import AuthContext
from app.auth.dependencies import require_permission
from app.db import get_db
from app.models.tokens import (
    CreateTokenRequest,
    CreateTokenResponse,
    ListTokensRequest,
    ListTokensResponse,
    RevokeTokenRequest,
    RevokeTokenResponse,
    TokenResponse,
)

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


@router.post("/create", response_model=CreateTokenResponse)
async def create_token(
    request: CreateTokenRequest,
    auth: AuthContext = Depends(require_permission("org.manage")),
    db: asyncpg.Connection = Depends(get_db),
) -> CreateTokenResponse:
    org_id = uuid.UUID(auth.org_id)
    user = await db.fetchrow(
        "SELECT id FROM users WHERE id = $1 AND org_id = $2 AND is_active = TRUE",
        request.user_id,
        org_id,
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    row = await db.fetchrow(
        """
        INSERT INTO api_tokens (org_id, user_id, token_hash, label, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, user_id, label, is_active, expires_at, created_at
        """,
        org_id,
        request.user_id,
        token_hash,
        request.label,
        request.expires_at,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create token",
        )

    return CreateTokenResponse(
        id=row["id"],
        token=raw_token,
        user_id=row["user_id"],
        label=row["label"],
        is_active=row["is_active"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


@router.post("/list", response_model=ListTokensResponse)
async def list_tokens(
    request: ListTokensRequest,
    auth: AuthContext = Depends(require_permission("org.manage")),
    db: asyncpg.Connection = Depends(get_db),
) -> ListTokensResponse:
    org_id = uuid.UUID(auth.org_id)
    query = """
        SELECT id, org_id, user_id, label, is_active, expires_at, last_used_at, created_at
        FROM api_tokens
        WHERE org_id = $1
    """
    params: list[object] = [org_id]
    next_param = 2

    if request.user_id is not None:
        query += f" AND user_id = ${next_param}"
        params.append(request.user_id)
        next_param += 1
    if request.is_active is not None:
        query += f" AND is_active = ${next_param}"
        params.append(request.is_active)

    query += " ORDER BY created_at DESC"
    rows = await db.fetch(query, *params)

    return ListTokensResponse(tokens=[TokenResponse(**dict(row)) for row in rows])


@router.post("/revoke", response_model=RevokeTokenResponse)
async def revoke_token(
    request: RevokeTokenRequest,
    auth: AuthContext = Depends(require_permission("org.manage")),
    db: asyncpg.Connection = Depends(get_db),
) -> RevokeTokenResponse:
    org_id = uuid.UUID(auth.org_id)
    row = await db.fetchrow(
        """
        UPDATE api_tokens
        SET is_active = FALSE
        WHERE id = $1 AND org_id = $2
        RETURNING id, is_active
        """,
        request.token_id,
        org_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")

    return RevokeTokenResponse(id=row["id"], is_active=row["is_active"])
