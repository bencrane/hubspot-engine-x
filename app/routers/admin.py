import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import require_super_admin
from app.db import get_db
from app.models.admin import (
    AdminCreateUserRequest,
    AdminUserResponse,
    CreateOrgRequest,
    OrgResponse,
)

router = APIRouter(prefix="/api/super-admin", tags=["super-admin"])


@router.post("/orgs", response_model=OrgResponse, dependencies=[Depends(require_super_admin)])
async def create_organization(
    request: CreateOrgRequest,
    db: asyncpg.Connection = Depends(get_db),
) -> OrgResponse:
    try:
        row = await db.fetchrow(
            """
            INSERT INTO organizations (name, slug)
            VALUES ($1, $2)
            RETURNING id, name, slug, is_active, created_at
            """,
            request.name,
            request.slug,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization with this slug already exists",
        ) from None

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create organization",
        )

    return OrgResponse(**dict(row))


@router.post("/users", response_model=AdminUserResponse, dependencies=[Depends(require_super_admin)])
async def create_super_admin_user(
    request: AdminCreateUserRequest,
    db: asyncpg.Connection = Depends(get_db),
) -> AdminUserResponse:
    org = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", request.org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    if request.role in {"company_admin", "company_member"}:
        if request.client_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="client_id is required for company-scoped roles",
            )
        client = await db.fetchrow(
            "SELECT id FROM clients WHERE id = $1 AND org_id = $2",
            request.client_id,
            request.org_id,
        )
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client not found in this organization",
            )
    elif request.client_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="org_admin users must not have a client_id",
        )

    try:
        row = await db.fetchrow(
            """
            INSERT INTO users (org_id, email, name, role, client_id)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, org_id, email, name, role, client_id, is_active, created_at
            """,
            request.org_id,
            request.email,
            request.name,
            request.role,
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

    return AdminUserResponse(**dict(row))
