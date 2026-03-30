"""Integration tests for all endpoints using FastAPI TestClient.

Mocks DB (asyncpg) and external APIs (HubSpot, Nango). Each endpoint:
happy path + auth denied.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set env vars BEFORE importing app modules
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("SUPER_ADMIN_JWT_SECRET", "test-super-admin-secret-long-32!!")
os.environ.setdefault("HUBSPOT_CLIENT_ID", "test-client-id")
os.environ.setdefault("HUBSPOT_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("NANGO_SECRET_KEY", "test-nango-key")
os.environ.setdefault("ALLOWED_ORIGINS", "")

from fastapi.testclient import TestClient

from app.auth.context import AuthContext, ROLE_PERMISSIONS
from app.auth.dependencies import get_current_auth, require_super_admin
from app.db import get_db

ORG_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000010"
CLIENT_ID = "00000000-0000-0000-0000-000000000002"
TOKEN_ID = "00000000-0000-0000-0000-000000000020"
MAPPING_ID = "00000000-0000-0000-0000-000000000040"

_ORG_ADMIN = AuthContext(
    org_id=ORG_ID,
    user_id=USER_ID,
    role="org_admin",
    permissions=ROLE_PERMISSIONS["org_admin"],
    client_id=None,
    auth_method="session",
)

_MEMBER = AuthContext(
    org_id=ORG_ID,
    user_id=USER_ID,
    role="company_member",
    permissions=ROLE_PERMISSIONS["company_member"],
    client_id=CLIENT_ID,
    auth_method="session",
)


@pytest.fixture
def mock_conn():
    """Mock asyncpg connection."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="UPDATE 1")
    return conn


@pytest.fixture
def mock_pool(mock_conn):
    """Mock asyncpg pool."""
    pool = MagicMock()

    @asynccontextmanager
    async def fake_acquire():
        yield mock_conn

    pool.acquire = fake_acquire
    pool.execute = AsyncMock()
    return pool


def _make_client(mock_conn, mock_pool, auth_context=None):
    """Create a TestClient with mocked deps."""
    from app.main import app

    async def override_get_db():
        yield mock_conn

    async def override_auth():
        return auth_context or _ORG_ADMIN

    async def override_super_admin():
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_auth] = override_auth

    with (
        patch("app.db.get_pool", return_value=mock_pool),
        patch("app.db._pool", mock_pool),
    ):
        tc = TestClient(app, raise_server_exceptions=False)
        yield tc

    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(mock_conn, mock_pool):
    """TestClient authenticated as org_admin."""
    yield from _make_client(mock_conn, mock_pool, _ORG_ADMIN)


@pytest.fixture
def member_client(mock_conn, mock_pool):
    """TestClient authenticated as company_member."""
    yield from _make_client(mock_conn, mock_pool, _MEMBER)


@pytest.fixture
def super_admin_client(mock_conn, mock_pool):
    """TestClient authenticated as super admin."""
    from app.main import app

    async def override_get_db():
        yield mock_conn

    async def override_super_admin():
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_super_admin] = override_super_admin

    with (
        patch("app.db.get_pool", return_value=mock_pool),
        patch("app.db._pool", mock_pool),
    ):
        tc = TestClient(app, raise_server_exceptions=False)
        yield tc

    app.dependency_overrides.clear()


@pytest.fixture
def noauth_client(mock_conn, mock_pool):
    """TestClient with no auth override — uses real auth (will fail)."""
    from app.main import app

    async def override_get_db():
        yield mock_conn

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch("app.db.get_pool", return_value=mock_pool),
        patch("app.db._pool", mock_pool),
    ):
        tc = TestClient(app, raise_server_exceptions=False)
        yield tc

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health(self, admin_client, mock_conn):
        mock_conn.execute = AsyncMock(return_value="SELECT 1")
        resp = admin_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


# ---------------------------------------------------------------------------
# Super-Admin
# ---------------------------------------------------------------------------


class TestSuperAdmin:
    def test_create_org(self, super_admin_client, mock_conn):
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": uuid.UUID(ORG_ID),
            "name": "Test Org",
            "slug": "test-org",
            "is_active": True,
            "created_at": now,
        })
        resp = super_admin_client.post(
            "/api/super-admin/orgs",
            json={"name": "Test Org", "slug": "test-org"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Org"

    def test_create_org_no_auth(self, noauth_client):
        resp = noauth_client.post(
            "/api/super-admin/orgs",
            json={"name": "Test Org", "slug": "test-org"},
        )
        assert resp.status_code == 401

    def test_create_user(self, super_admin_client, mock_conn):
        now = datetime.now(timezone.utc)
        call_count = 0

        async def mock_fetchrow(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"id": uuid.UUID(ORG_ID)}  # org lookup
            return {
                "id": uuid.uuid4(),
                "org_id": uuid.UUID(ORG_ID),
                "email": "test@example.com",
                "name": "Test User",
                "role": "org_admin",
                "client_id": None,
                "is_active": True,
                "created_at": now,
            }

        mock_conn.fetchrow = mock_fetchrow

        resp = super_admin_client.post(
            "/api/super-admin/users",
            json={
                "org_id": ORG_ID,
                "email": "test@example.com",
                "name": "Test User",
                "role": "org_admin",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAuthEndpoints:
    def test_me(self, admin_client):
        resp = admin_client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["org_id"] == ORG_ID
        assert data["role"] == "org_admin"

    def test_me_no_auth(self, noauth_client):
        resp = noauth_client.get("/api/auth/me")
        assert resp.status_code == 401



# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class TestClientEndpoints:
    def test_create_client(self, admin_client, mock_conn):
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": uuid.UUID(CLIENT_ID),
            "org_id": uuid.UUID(ORG_ID),
            "name": "Acme Corp",
            "domain": "acme.com",
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        })
        resp = admin_client.post(
            "/api/clients/create",
            json={"name": "Acme Corp", "domain": "acme.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Acme Corp"

    def test_create_client_no_permission(self, member_client):
        resp = member_client.post(
            "/api/clients/create",
            json={"name": "Acme Corp"},
        )
        assert resp.status_code == 403

    def test_list_clients(self, admin_client, mock_conn):
        mock_conn.fetch = AsyncMock(return_value=[])
        resp = admin_client.post("/api/clients/list", json={})
        assert resp.status_code == 200
        assert resp.json()["clients"] == []

    def test_get_client(self, admin_client, mock_conn):
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": uuid.UUID(CLIENT_ID),
            "org_id": uuid.UUID(ORG_ID),
            "name": "Acme",
            "domain": None,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        })
        resp = admin_client.post(
            "/api/clients/get",
            json={"client_id": CLIENT_ID},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class TestUserEndpoints:
    def test_create_user(self, admin_client, mock_conn):
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": uuid.uuid4(),
            "org_id": uuid.UUID(ORG_ID),
            "email": "new@example.com",
            "name": "New User",
            "role": "org_admin",
            "client_id": None,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        })

        resp = admin_client.post(
            "/api/users/create",
            json={
                "email": "new@example.com",
                "name": "New User",
                "role": "org_admin",
            },
        )
        assert resp.status_code == 200

    def test_list_users(self, admin_client, mock_conn):
        mock_conn.fetch = AsyncMock(return_value=[])
        resp = admin_client.post("/api/users/list", json={})
        assert resp.status_code == 200

    def test_create_user_no_permission(self, member_client):
        resp = member_client.post(
            "/api/users/create",
            json={
                "email": "test@test.com",
                "role": "org_admin",
            },
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# API Tokens
# ---------------------------------------------------------------------------


class TestTokenEndpoints:
    def test_create_token(self, admin_client, mock_conn):
        now = datetime.now(timezone.utc)
        call_count = 0

        async def mock_fetchrow(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"id": uuid.UUID(USER_ID)}  # user lookup
            return {
                "id": uuid.uuid4(),
                "user_id": uuid.UUID(USER_ID),
                "label": "test-token",
                "is_active": True,
                "expires_at": None,
                "created_at": now,
            }

        mock_conn.fetchrow = mock_fetchrow

        resp = admin_client.post(
            "/api/tokens/create",
            json={"user_id": USER_ID, "label": "test-token"},
        )
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_list_tokens(self, admin_client, mock_conn):
        mock_conn.fetch = AsyncMock(return_value=[])
        resp = admin_client.post("/api/tokens/list", json={})
        assert resp.status_code == 200

    def test_revoke_token(self, admin_client, mock_conn):
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": uuid.UUID(TOKEN_ID),
            "is_active": False,
        })
        resp = admin_client.post(
            "/api/tokens/revoke",
            json={"token_id": TOKEN_ID},
        )
        assert resp.status_code == 200

    def test_tokens_no_permission(self, member_client):
        resp = member_client.post("/api/tokens/list", json={})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


class TestConnectionEndpoints:
    def test_create_connection(self, admin_client, mock_conn, mock_pool):
        # validate_client_access → find client; existing connection check → None; insert → row
        mock_conn.fetchrow = AsyncMock(side_effect=[
            {"id": uuid.UUID(CLIENT_ID)},  # validate_client_access
            None,  # existing connection check
            {  # INSERT RETURNING
                "id": uuid.uuid4(),
                "org_id": uuid.UUID(ORG_ID),
                "client_id": uuid.UUID(CLIENT_ID),
                "nango_connection_id": CLIENT_ID,
                "status": "pending",
                "hub_domain": None,
                "hubspot_portal_id": None,
                "scopes": None,
                "last_used_at": None,
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        ])

        with patch(
            "app.services.token_manager.create_connect_session",
            new_callable=AsyncMock,
            return_value={"token": "sess-token", "connect_link": "https://nango.dev/connect", "expires_at": "2026-01-01"},
        ):
            resp = admin_client.post(
                "/api/connections/create",
                json={"client_id": CLIENT_ID},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_token"] == "sess-token"

    def test_list_connections(self, admin_client, mock_conn):
        mock_conn.fetch = AsyncMock(return_value=[])
        resp = admin_client.post("/api/connections/list", json={})
        assert resp.status_code == 200

    def test_connections_no_write_permission(self, member_client):
        resp = member_client.post(
            "/api/connections/create",
            json={"client_id": CLIENT_ID},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CRM Read
# ---------------------------------------------------------------------------


class TestCrmEndpoints:
    def test_search(self, admin_client, mock_conn, mock_pool):
        mock_conn.fetchrow = AsyncMock(
            return_value={"nango_connection_id": "nango-conn"}
        )

        with patch.object(
            __import__("app.services.hubspot", fromlist=["HubSpotClient"]).HubSpotClient,
            "search_objects",
            new_callable=AsyncMock,
            return_value={"total": 0, "results": [], "paging": {}},
        ):
            resp = admin_client.post(
                "/api/crm/search",
                json={"client_id": CLIENT_ID, "object_type": "contacts"},
            )
        assert resp.status_code == 200

    def test_crm_no_auth(self, noauth_client):
        resp = noauth_client.post(
            "/api/crm/search",
            json={"client_id": CLIENT_ID, "object_type": "contacts"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Field Mappings
# ---------------------------------------------------------------------------


class TestFieldMappingEndpoints:
    def test_set_field_mapping(self, admin_client, mock_conn, mock_pool):
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": uuid.UUID(MAPPING_ID),
            "org_id": uuid.UUID(ORG_ID),
            "client_id": uuid.UUID(CLIENT_ID),
            "canonical_object": "contact",
            "canonical_field": "email_address",
            "hubspot_object": "contacts",
            "hubspot_property": "email",
            "transform_rule": None,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        })

        resp = admin_client.post(
            "/api/field-mappings/set",
            json={
                "client_id": CLIENT_ID,
                "canonical_object": "contact",
                "canonical_field": "email_address",
                "hubspot_object": "contacts",
                "hubspot_property": "email",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["canonical_field"] == "email_address"

    def test_list_field_mappings(self, admin_client, mock_conn, mock_pool):
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchrow = AsyncMock(return_value={"id": CLIENT_ID})

        resp = admin_client.post(
            "/api/field-mappings/list",
            json={"client_id": CLIENT_ID},
        )
        assert resp.status_code == 200

    def test_field_mapping_no_permission(self, member_client):
        resp = member_client.post(
            "/api/field-mappings/set",
            json={
                "client_id": CLIENT_ID,
                "canonical_object": "contact",
                "canonical_field": "email",
                "hubspot_object": "contacts",
                "hubspot_property": "email",
            },
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------


class TestPushEndpoints:
    def test_push_records(self, admin_client, mock_conn, mock_pool):
        mock_conn.fetchrow = AsyncMock(side_effect=[
            {"id": uuid.UUID(CLIENT_ID)},  # validate_client_access
            {"nango_connection_id": "nango-conn"},  # resolve_connection
            {"id": uuid.uuid4()},  # connection UUID lookup in log_push
            {"id": uuid.uuid4()},  # INSERT push log
        ])
        mock_conn.fetch = AsyncMock(return_value=[])  # empty field mappings

        with patch(
            "app.services.hubspot.token_manager.get_valid_token",
            new_callable=AsyncMock,
            return_value="hs-token",
        ), patch.object(
            __import__("app.services.hubspot", fromlist=["HubSpotClient"]).HubSpotClient,
            "batch_upsert",
            new_callable=AsyncMock,
            return_value={"results": [{"id": "1"}], "errors": []},
        ):
            resp = admin_client.post(
                "/api/push/records",
                json={
                    "client_id": CLIENT_ID,
                    "object_type": "contacts",
                    "records": [{"properties": {"email": "test@test.com"}}],
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

    def test_push_no_permission(self, member_client):
        resp = member_client.post(
            "/api/push/records",
            json={
                "client_id": CLIENT_ID,
                "object_type": "contacts",
                "records": [{"properties": {"email": "x"}}],
            },
        )
        assert resp.status_code == 403
