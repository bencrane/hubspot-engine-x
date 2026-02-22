import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.context import AuthContext
from app.auth.dependencies import require_permission
from app.auth.passwords import hash_password
from app.db import get_db
from app.models.users import CreateUserRequest, ListUsersRequest, ListUsersResponse, UserResponse

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/create", response_model=UserResponse)
async def create_user(
    request: CreateUserRequest,
    auth: AuthContext = Depends(require_permission("org.manage")),
    db: asyncpg.Connection = Depends(get_db),
) -> UserResponse:
    org_id = uuid.UUID(auth.org_id)

    if request.role in {"company_admin", "company_member"}:
        if request.client_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="client_id is required for company-scoped roles",
            )
        client = await db.fetchrow(
            "SELECT id FROM clients WHERE id = $1 AND org_id = $2",
            request.client_id,
            org_id,
        )
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    elif request.client_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="org_admin users must not have a client_id",
        )

    password_hash = hash_password(request.password)
    try:
        row = await db.fetchrow(
            """
            INSERT INTO users (org_id, email, name, role, password_hash, client_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, org_id, email, name, role, client_id, is_active, created_at, updated_at
            """,
            org_id,
            request.email,
            request.name,
            request.role,
            password_hash,
            request.client_id,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists in this organization",
        ) from None

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user",
        )

    return UserResponse(**dict(row))


@router.post("/list", response_model=ListUsersResponse)
async def list_users(
    request: ListUsersRequest,
    auth: AuthContext = Depends(require_permission("org.manage")),
    db: asyncpg.Connection = Depends(get_db),
) -> ListUsersResponse:
    org_id = uuid.UUID(auth.org_id)
    query = """
        SELECT id, org_id, email, name, role, client_id, is_active, created_at, updated_at
        FROM users
        WHERE org_id = $1
    """
    params: list[object] = [org_id]
    next_param = 2

    if request.client_id is not None:
        query += f" AND client_id = ${next_param}"
        params.append(request.client_id)
        next_param += 1
    if request.role is not None:
        query += f" AND role = ${next_param}"
        params.append(request.role)
        next_param += 1
    if request.is_active is not None:
        query += f" AND is_active = ${next_param}"
        params.append(request.is_active)

    query += " ORDER BY created_at DESC"
    rows = await db.fetch(query, *params)

    return ListUsersResponse(users=[UserResponse(**dict(row)) for row in rows])
