import asyncio
import hashlib
import hmac
from collections.abc import Callable

import asyncpg
import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import InvalidTokenError, PyJWKClient

from app.auth.context import AuthContext, ROLE_PERMISSIONS
from app.config import settings
from app.db import get_pool

_jwks_client = PyJWKClient(
    "https://api.authengine.dev/api/auth/jwks",
    cache_jwk_set=True,
    lifespan=300,
)


def _extract_bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )

    return token.strip()


async def _touch_api_token_last_used(pool: asyncpg.Pool, token_id: str) -> None:
    try:
        await pool.execute(
            "UPDATE api_tokens SET last_used_at = NOW() WHERE id = $1",
            token_id,
        )
    except Exception:
        # Best-effort telemetry update; auth success must not depend on this write.
        return


async def get_current_auth(request: Request) -> AuthContext:
    token = _extract_bearer_token(request)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    pool = get_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT
                t.id AS token_id,
                t.org_id,
                t.user_id,
                u.role,
                u.client_id
            FROM api_tokens t
            INNER JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = $1
              AND t.is_active = TRUE
              AND u.is_active = TRUE
              AND (t.expires_at IS NULL OR t.expires_at > NOW())
            """,
            token_hash,
        )

    if row is not None:
        role = str(row["role"])
        permissions = ROLE_PERMISSIONS.get(role)
        if permissions is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing authentication token",
            )

        asyncio.create_task(_touch_api_token_last_used(pool, str(row["token_id"])))
        return AuthContext(
            org_id=str(row["org_id"]),
            user_id=str(row["user_id"]),
            role=role,
            permissions=permissions,
            client_id=str(row["client_id"]) if row["client_id"] is not None else None,
            auth_method="api_token",
        )

    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["EdDSA"],
            issuer="https://api.authengine.dev",
            audience="https://api.authengine.dev",
            options={"require": ["exp", "sub", "org_id", "role"]},
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        ) from None

    org_id = claims.get("org_id")
    user_id = claims.get("sub")
    role = claims.get("role")
    client_id = claims.get("client_id")

    if not org_id or not user_id or not role or role not in ROLE_PERMISSIONS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )

    return AuthContext(
        org_id=str(org_id),
        user_id=str(user_id),
        role=str(role),
        permissions=ROLE_PERMISSIONS[str(role)],
        client_id=str(client_id) if client_id is not None else None,
        auth_method="session",
    )


async def require_super_admin(request: Request) -> None:
    token = _extract_bearer_token(request)
    if not hmac.compare_digest(token, settings.SUPER_ADMIN_JWT_SECRET):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token",
        )


async def validate_client_access(auth: AuthContext, client_id: str, pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection:
        client = await connection.fetchrow(
            "SELECT id FROM clients WHERE id = $1 AND org_id = $2",
            client_id,
            auth.org_id,
        )

    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    if auth.client_id is not None and auth.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this client",
        )


def require_permission(permission: str) -> Callable[..., object]:
    async def permission_dependency(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        if permission not in auth.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return auth

    return permission_dependency
