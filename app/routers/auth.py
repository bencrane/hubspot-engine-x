from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.context import AuthContext
from app.auth.dependencies import get_current_auth
from app.auth.passwords import verify_password
from app.config import settings
from app.db import get_db
from app.models.auth import LoginRequest, LoginResponse, LoginUserDetail, MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    db: asyncpg.Connection = Depends(get_db),
) -> LoginResponse:
    rows = await db.fetch(
        """
        SELECT
            u.id,
            u.org_id,
            u.email,
            u.name,
            u.role,
            u.client_id,
            u.password_hash
        FROM users u
        INNER JOIN organizations o ON o.id = u.org_id
        WHERE u.email = $1
          AND u.is_active = TRUE
          AND o.is_active = TRUE
        """,
        request.email,
    )

    if len(rows) != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    row = rows[0]
    password_hash = row["password_hash"]
    if password_hash is None or not verify_password(request.password, str(password_hash)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    payload = {
        "org_id": str(row["org_id"]),
        "user_id": str(row["id"]),
        "role": str(row["role"]),
        "client_id": str(row["client_id"]) if row["client_id"] is not None else None,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")

    return LoginResponse(
        token=token,
        user=LoginUserDetail(
            id=row["id"],
            org_id=row["org_id"],
            email=row["email"],
            name=row["name"],
            role=str(row["role"]),
            client_id=row["client_id"],
        ),
    )


@router.get("/me", response_model=MeResponse)
async def me(auth: AuthContext = Depends(get_current_auth)) -> MeResponse:
    return MeResponse(
        org_id=auth.org_id,
        user_id=auth.user_id,
        role=auth.role,
        permissions=auth.permissions,
        client_id=auth.client_id,
        auth_method=auth.auth_method,
    )
