"""Shared test fixtures for hubspot-engine-x."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.auth.context import AuthContext, ROLE_PERMISSIONS


@pytest.fixture
def org_admin_auth() -> AuthContext:
    return AuthContext(
        org_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000010",
        role="org_admin",
        permissions=ROLE_PERMISSIONS["org_admin"],
        client_id=None,
        auth_method="api_token",
    )


@pytest.fixture
def mock_db() -> AsyncMock:
    """Mock asyncpg connection."""
    db = AsyncMock()
    db.fetchrow = AsyncMock(return_value=None)
    db.fetch = AsyncMock(return_value=[])
    db.execute = AsyncMock(return_value="UPDATE 1")
    return db


@pytest.fixture
def hubspot_search_response() -> dict:
    return {
        "total": 2,
        "results": [
            {
                "id": "101",
                "properties": {"email": "alice@example.com", "firstname": "Alice"},
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-02T00:00:00Z",
            },
            {
                "id": "102",
                "properties": {"email": "bob@example.com", "firstname": "Bob"},
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-02T00:00:00Z",
            },
        ],
        "paging": {"next": {"after": "102"}},
    }


@pytest.fixture
def hubspot_batch_upsert_response() -> dict:
    return {
        "results": [
            {"id": "201", "properties": {"email": "alice@example.com"}},
            {"id": "202", "properties": {"email": "bob@example.com"}},
        ],
        "errors": [],
    }
