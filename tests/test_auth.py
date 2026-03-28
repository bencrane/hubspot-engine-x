"""Tests for auth dependencies (app/auth/dependencies.py).

Covers: API token auth, JWT auth, super-admin auth, RBAC, client access, multi-tenancy.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from app.auth.context import AuthContext, ROLE_PERMISSIONS
from app.auth.dependencies import (
    get_current_auth,
    require_permission,
    require_super_admin,
    validate_client_access,
)

ORG_ID = str(uuid4())
USER_ID = str(uuid4())
CLIENT_ID = str(uuid4())
TOKEN_ID = str(uuid4())
SUPER_ADMIN_SECRET = "test-super-admin-secret-long-32!!"

_TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
_TEST_PUBLIC_KEY = _TEST_PRIVATE_KEY.public_key()


def _mock_signing_key():
    key = MagicMock()
    key.key = _TEST_PUBLIC_KEY
    return key


def _make_request(token: str) -> MagicMock:
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {token}"}
    return request


def _make_request_no_auth() -> MagicMock:
    request = MagicMock()
    request.headers = {}
    return request


def _make_jwt(claims: dict, private_key=None) -> str:
    if private_key is None:
        private_key = _TEST_PRIVATE_KEY
    return jwt.encode(claims, private_key, algorithm="EdDSA")


def _valid_jwt_claims(**overrides: object) -> dict:
    claims = {
        "org_id": ORG_ID,
        "sub": USER_ID,
        "role": "org_admin",
        "client_id": None,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        "iss": "https://api.authengine.dev",
        "aud": "https://api.authengine.dev",
    }
    claims.update(overrides)
    return claims


def _token_db_row(role: str = "org_admin", client_id: str | None = None) -> dict:
    return {
        "token_id": TOKEN_ID,
        "org_id": ORG_ID,
        "user_id": USER_ID,
        "role": role,
        "client_id": client_id,
    }


def _make_pool_with_row(row):
    """Create a mock asyncpg pool that returns `row` from fetchrow."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=row)

    mock_pool = MagicMock()

    @asynccontextmanager
    async def fake_acquire():
        yield mock_conn

    mock_pool.acquire = fake_acquire
    mock_pool.execute = AsyncMock()
    return mock_pool


# ---------------------------------------------------------------------------
# Super-Admin Auth
# ---------------------------------------------------------------------------


class TestSuperAdminAuth:
    @pytest.mark.asyncio
    async def test_valid_super_admin(self):
        request = _make_request(SUPER_ADMIN_SECRET)
        with patch("app.auth.dependencies.settings") as mock_settings:
            mock_settings.SUPER_ADMIN_JWT_SECRET = SUPER_ADMIN_SECRET
            await require_super_admin(request)  # should not raise

    @pytest.mark.asyncio
    async def test_wrong_super_admin_token(self):
        request = _make_request("wrong-token")
        with patch("app.auth.dependencies.settings") as mock_settings:
            mock_settings.SUPER_ADMIN_JWT_SECRET = SUPER_ADMIN_SECRET
            with pytest.raises(HTTPException) as exc_info:
                await require_super_admin(request)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_auth_header(self):
        request = _make_request_no_auth()
        with pytest.raises(HTTPException) as exc_info:
            await require_super_admin(request)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# API Token Auth
# ---------------------------------------------------------------------------


class TestAPITokenAuth:
    @pytest.mark.asyncio
    async def test_valid_api_token(self):
        raw_token = "test-raw-token-value"
        mock_pool = _make_pool_with_row(_token_db_row())

        request = _make_request(raw_token)
        with patch("app.auth.dependencies.get_pool", return_value=mock_pool):
            auth = await get_current_auth(request)

        assert auth.org_id == ORG_ID
        assert auth.user_id == USER_ID
        assert auth.role == "org_admin"
        assert auth.auth_method == "api_token"
        assert auth.permissions == ROLE_PERMISSIONS["org_admin"]

    @pytest.mark.asyncio
    async def test_inactive_token_falls_through_to_jwt(self):
        """Token not found in DB -> falls through to JWT parsing."""
        raw_token = _make_jwt(_valid_jwt_claims())
        request = _make_request(raw_token)

        mock_pool = _make_pool_with_row(None)

        with (
            patch("app.auth.dependencies.get_pool", return_value=mock_pool),
            patch("app.auth.dependencies._jwks_client") as mock_jwks,
        ):
            mock_jwks.get_signing_key_from_jwt.return_value = _mock_signing_key()
            auth = await get_current_auth(request)

        assert auth.auth_method == "session"
        assert auth.org_id == ORG_ID


# ---------------------------------------------------------------------------
# JWT Auth
# ---------------------------------------------------------------------------


class TestJWTAuth:
    @pytest.mark.asyncio
    async def test_valid_jwt(self):
        token = _make_jwt(_valid_jwt_claims())
        request = _make_request(token)
        mock_pool = _make_pool_with_row(None)

        with (
            patch("app.auth.dependencies.get_pool", return_value=mock_pool),
            patch("app.auth.dependencies._jwks_client") as mock_jwks,
        ):
            mock_jwks.get_signing_key_from_jwt.return_value = _mock_signing_key()
            auth = await get_current_auth(request)

        assert auth.org_id == ORG_ID
        assert auth.user_id == USER_ID
        assert auth.role == "org_admin"
        assert auth.auth_method == "session"

    @pytest.mark.asyncio
    async def test_expired_jwt(self):
        claims = _valid_jwt_claims(exp=datetime.now(timezone.utc) - timedelta(hours=1))
        token = _make_jwt(claims)
        request = _make_request(token)
        mock_pool = _make_pool_with_row(None)

        with (
            patch("app.auth.dependencies.get_pool", return_value=mock_pool),
            patch("app.auth.dependencies._jwks_client") as mock_jwks,
        ):
            mock_jwks.get_signing_key_from_jwt.return_value = _mock_signing_key()
            with pytest.raises(HTTPException) as exc_info:
                await get_current_auth(request)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_claims(self):
        """JWT missing org_id should be rejected."""
        claims = _valid_jwt_claims()
        del claims["org_id"]
        token = _make_jwt(claims)
        request = _make_request(token)
        mock_pool = _make_pool_with_row(None)

        with (
            patch("app.auth.dependencies.get_pool", return_value=mock_pool),
            patch("app.auth.dependencies._jwks_client") as mock_jwks,
        ):
            mock_jwks.get_signing_key_from_jwt.return_value = _mock_signing_key()
            with pytest.raises(HTTPException) as exc_info:
                await get_current_auth(request)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_role(self):
        claims = _valid_jwt_claims(role="nonexistent_role")
        token = _make_jwt(claims)
        request = _make_request(token)
        mock_pool = _make_pool_with_row(None)

        with (
            patch("app.auth.dependencies.get_pool", return_value=mock_pool),
            patch("app.auth.dependencies._jwks_client") as mock_jwks,
        ):
            mock_jwks.get_signing_key_from_jwt.return_value = _mock_signing_key()
            with pytest.raises(HTTPException) as exc_info:
                await get_current_auth(request)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_signing_key(self):
        wrong_key = Ed25519PrivateKey.generate()
        token = _make_jwt(_valid_jwt_claims(), private_key=wrong_key)
        request = _make_request(token)
        mock_pool = _make_pool_with_row(None)

        with (
            patch("app.auth.dependencies.get_pool", return_value=mock_pool),
            patch("app.auth.dependencies._jwks_client") as mock_jwks,
        ):
            mock_jwks.get_signing_key_from_jwt.return_value = _mock_signing_key()
            with pytest.raises(HTTPException) as exc_info:
                await get_current_auth(request)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_jwt_with_client_id(self):
        claims = _valid_jwt_claims(role="company_admin", client_id=CLIENT_ID)
        token = _make_jwt(claims)
        request = _make_request(token)
        mock_pool = _make_pool_with_row(None)

        with (
            patch("app.auth.dependencies.get_pool", return_value=mock_pool),
            patch("app.auth.dependencies._jwks_client") as mock_jwks,
        ):
            mock_jwks.get_signing_key_from_jwt.return_value = _mock_signing_key()
            auth = await get_current_auth(request)

        assert auth.client_id == CLIENT_ID
        assert auth.role == "company_admin"
        assert auth.permissions == ROLE_PERMISSIONS["company_admin"]


# ---------------------------------------------------------------------------
# RBAC (require_permission)
# ---------------------------------------------------------------------------


class TestRBAC:
    @pytest.mark.asyncio
    async def test_permission_granted(self):
        auth = AuthContext(
            org_id=ORG_ID,
            user_id=USER_ID,
            role="org_admin",
            permissions=ROLE_PERMISSIONS["org_admin"],
            client_id=None,
            auth_method="api_token",
        )
        dep = require_permission("push.write")
        result = await dep(auth=auth)
        assert result.org_id == ORG_ID

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        auth = AuthContext(
            org_id=ORG_ID,
            user_id=USER_ID,
            role="company_member",
            permissions=ROLE_PERMISSIONS["company_member"],
            client_id=CLIENT_ID,
            auth_method="session",
        )
        dep = require_permission("push.write")
        with pytest.raises(HTTPException) as exc_info:
            await dep(auth=auth)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_company_admin_cannot_push(self):
        auth = AuthContext(
            org_id=ORG_ID,
            user_id=USER_ID,
            role="company_admin",
            permissions=ROLE_PERMISSIONS["company_admin"],
            client_id=CLIENT_ID,
            auth_method="session",
        )
        dep = require_permission("push.write")
        with pytest.raises(HTTPException) as exc_info:
            await dep(auth=auth)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_company_admin_can_read_topology(self):
        auth = AuthContext(
            org_id=ORG_ID,
            user_id=USER_ID,
            role="company_admin",
            permissions=ROLE_PERMISSIONS["company_admin"],
            client_id=CLIENT_ID,
            auth_method="session",
        )
        dep = require_permission("topology.read")
        result = await dep(auth=auth)
        assert result.role == "company_admin"


# ---------------------------------------------------------------------------
# Client Access Validation
# ---------------------------------------------------------------------------


class TestClientAccess:
    @pytest.mark.asyncio
    async def test_client_belongs_to_org(self):
        auth = AuthContext(
            org_id=ORG_ID,
            user_id=USER_ID,
            role="org_admin",
            permissions=ROLE_PERMISSIONS["org_admin"],
            client_id=None,
            auth_method="api_token",
        )
        mock_pool = _make_pool_with_row({"id": CLIENT_ID})
        await validate_client_access(auth, CLIENT_ID, mock_pool)  # should not raise

    @pytest.mark.asyncio
    async def test_client_not_found(self):
        auth = AuthContext(
            org_id=ORG_ID,
            user_id=USER_ID,
            role="org_admin",
            permissions=ROLE_PERMISSIONS["org_admin"],
            client_id=None,
            auth_method="api_token",
        )
        mock_pool = _make_pool_with_row(None)

        with pytest.raises(HTTPException) as exc_info:
            await validate_client_access(auth, CLIENT_ID, mock_pool)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_company_user_wrong_client(self):
        """Company-scoped user trying to access a different client."""
        other_client = str(uuid4())
        auth = AuthContext(
            org_id=ORG_ID,
            user_id=USER_ID,
            role="company_admin",
            permissions=ROLE_PERMISSIONS["company_admin"],
            client_id=CLIENT_ID,
            auth_method="session",
        )
        mock_pool = _make_pool_with_row({"id": other_client})

        with pytest.raises(HTTPException) as exc_info:
            await validate_client_access(auth, other_client, mock_pool)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_company_user_own_client(self):
        """Company-scoped user accessing their own client -- allowed."""
        auth = AuthContext(
            org_id=ORG_ID,
            user_id=USER_ID,
            role="company_admin",
            permissions=ROLE_PERMISSIONS["company_admin"],
            client_id=CLIENT_ID,
            auth_method="session",
        )
        mock_pool = _make_pool_with_row({"id": CLIENT_ID})
        await validate_client_access(auth, CLIENT_ID, mock_pool)  # should not raise
