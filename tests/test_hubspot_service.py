"""Tests for the HubSpot API client (app/services/hubspot.py)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.auth.context import AuthContext, ROLE_PERMISSIONS
from app.services.hubspot import (
    HubSpotAPIError,
    HubSpotClient,
    _parse_hubspot_error,
    _record_request,
    _rate_tracker,
    resolve_connection,
)


@pytest.fixture(autouse=True)
def clear_rate_tracker():
    """Reset rate tracking between tests."""
    _rate_tracker.clear()
    yield
    _rate_tracker.clear()


class TestParseHubSpotError:
    def test_structured_error(self):
        response = MagicMock(spec=httpx.Response)
        response.status_code = 400
        response.text = "bad request"
        response.json.return_value = {
            "category": "VALIDATION_ERROR",
            "message": "Property 'xyz' does not exist",
            "correlationId": "abc-123",
        }

        err = _parse_hubspot_error(response)

        assert err.status_code == 400
        assert err.category == "VALIDATION_ERROR"
        assert err.message == "Property 'xyz' does not exist"
        assert err.correlation_id == "abc-123"

    def test_unparseable_error(self):
        response = MagicMock(spec=httpx.Response)
        response.status_code = 500
        response.text = "Internal Server Error"
        response.json.side_effect = Exception("not json")

        err = _parse_hubspot_error(response)

        assert err.status_code == 500
        assert err.category == "UNKNOWN"
        assert err.message == "Internal Server Error"
        assert err.correlation_id is None


class TestHubSpotClient:
    @pytest.mark.asyncio
    async def test_get_token_caches(self):
        client = HubSpotClient("conn-1")

        with patch(
            "app.services.hubspot.token_manager.get_valid_token",
            new_callable=AsyncMock,
            return_value="test-token-123",
        ) as mock_get:
            token1 = await client._get_token()
            token2 = await client._get_token()

        assert token1 == "test-token-123"
        assert token2 == "test-token-123"
        mock_get.assert_called_once_with("conn-1")

    @pytest.mark.asyncio
    async def test_clear_token_cache(self):
        client = HubSpotClient("conn-1")

        with patch(
            "app.services.hubspot.token_manager.get_valid_token",
            new_callable=AsyncMock,
            return_value="token-v1",
        ):
            await client._get_token()

        client._clear_token_cache()
        assert client._cached_token is None

    @pytest.mark.asyncio
    async def test_request_success(self, hubspot_search_response):
        client = HubSpotClient("conn-1")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = '{"results": []}'
        mock_response.json.return_value = hubspot_search_response

        with (
            patch(
                "app.services.hubspot.token_manager.get_valid_token",
                new_callable=AsyncMock,
                return_value="test-token",
            ),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_http

            result = await client._request("GET", "/crm/v3/objects/contacts")

        assert result["total"] == 2
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_request_raises_on_400(self):
        client = HubSpotClient("conn-1")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = "bad"
        mock_response.json.return_value = {
            "category": "VALIDATION_ERROR",
            "message": "Invalid property",
        }
        mock_response.headers = {}

        with (
            patch(
                "app.services.hubspot.token_manager.get_valid_token",
                new_callable=AsyncMock,
                return_value="test-token",
            ),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_http

            with pytest.raises(HubSpotAPIError) as exc_info:
                await client._request("GET", "/crm/v3/objects/contacts")

            assert exc_info.value.status_code == 400
            assert exc_info.value.category == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_request_with_shared_http_client(self, hubspot_search_response):
        """When an http_client is passed, it should be used instead of creating a new one."""
        mock_http = AsyncMock()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = '{"results": []}'
        mock_response.json.return_value = hubspot_search_response
        mock_http.request = AsyncMock(return_value=mock_response)

        client = HubSpotClient("conn-1", http_client=mock_http)

        with patch(
            "app.services.hubspot.token_manager.get_valid_token",
            new_callable=AsyncMock,
            return_value="test-token",
        ):
            result = await client._request("GET", "/crm/v3/objects/contacts")

        assert result["total"] == 2
        mock_http.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_objects(self):
        client = HubSpotClient("conn-1")
        expected = {"results": [{"id": "1"}], "paging": {}}

        with patch.object(client, "_request", new_callable=AsyncMock, return_value=expected):
            result = await client.list_objects("contacts", properties=["email"])

        assert result == expected

    @pytest.mark.asyncio
    async def test_search_objects(self):
        client = HubSpotClient("conn-1")
        expected = {"total": 1, "results": [{"id": "1"}]}

        with patch.object(client, "_request", new_callable=AsyncMock, return_value=expected):
            result = await client.search_objects(
                "contacts",
                filter_groups=[{"filters": [{"propertyName": "email", "operator": "EQ", "value": "x@y.com"}]}],
                properties=["email"],
            )

        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_batch_read(self):
        client = HubSpotClient("conn-1")
        expected = {"results": [{"id": "1"}, {"id": "2"}]}

        with patch.object(client, "_request", new_callable=AsyncMock, return_value=expected):
            result = await client.batch_read("contacts", ["1", "2"], properties=["email"])

        assert len(result["results"]) == 2


class TestRateTracking:
    def test_record_request(self):
        _record_request("test-conn")
        assert len(_rate_tracker["test-conn"]) == 1

    def test_multiple_requests(self):
        for _ in range(10):
            _record_request("test-conn")
        assert len(_rate_tracker["test-conn"]) == 10


# ---------------------------------------------------------------------------
# Pagination + max_pages tests (item 15)
# ---------------------------------------------------------------------------


class TestPagination:
    @pytest.mark.asyncio
    async def test_multi_page_pagination(self):
        """_paginate should follow paging.next.after cursor across pages."""
        client = HubSpotClient("conn-1")
        call_count = 0

        async def mock_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "results": [{"id": "1"}, {"id": "2"}],
                    "paging": {"next": {"after": "2"}},
                }
            return {
                "results": [{"id": "3"}],
                "paging": {},
            }

        with patch.object(client, "_request", side_effect=mock_request):
            all_results = await client._fetch_all("/crm/v3/objects/contacts")

        assert len(all_results) == 3
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_empty_pagination(self):
        """Empty results should return empty list."""
        client = HubSpotClient("conn-1")

        async def mock_request(method, path, **kwargs):
            return {"results": [], "paging": {}}

        with patch.object(client, "_request", side_effect=mock_request):
            all_results = await client._fetch_all("/crm/v3/objects/contacts")

        assert all_results == []

    @pytest.mark.asyncio
    async def test_max_pages_limit(self):
        """_fetch_all should stop at max_pages."""
        client = HubSpotClient("conn-1")
        call_count = 0

        async def mock_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "results": [{"id": str(call_count)}],
                "paging": {"next": {"after": str(call_count)}},
            }

        with patch.object(client, "_request", side_effect=mock_request):
            all_results = await client._fetch_all(
                "/crm/v3/objects/contacts", max_pages=3
            )

        assert len(all_results) == 3
        assert call_count == 3


# ---------------------------------------------------------------------------
# resolve_connection tests (item 15)
# ---------------------------------------------------------------------------


class TestResolveConnection:
    @pytest.mark.asyncio
    async def test_connection_found(self):
        auth = AuthContext(
            org_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000010",
            role="org_admin",
            permissions=ROLE_PERMISSIONS["org_admin"],
            client_id=None,
            auth_method="api_token",
        )
        client_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

        mock_db = AsyncMock()
        mock_db.fetchrow = AsyncMock(
            return_value={"nango_connection_id": "nango-conn-123"}
        )

        result = await resolve_connection(auth, client_id, mock_db)
        assert result == "nango-conn-123"

    @pytest.mark.asyncio
    async def test_connection_not_found(self):
        auth = AuthContext(
            org_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000010",
            role="org_admin",
            permissions=ROLE_PERMISSIONS["org_admin"],
            client_id=None,
            auth_method="api_token",
        )
        client_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

        mock_db = AsyncMock()
        mock_db.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await resolve_connection(auth, client_id, mock_db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_connection_wrong_org(self):
        """resolve_connection filters by org_id — different org should 404."""
        auth = AuthContext(
            org_id="00000000-0000-0000-0000-000000000099",  # different org
            user_id="00000000-0000-0000-0000-000000000010",
            role="org_admin",
            permissions=ROLE_PERMISSIONS["org_admin"],
            client_id=None,
            auth_method="api_token",
        )
        client_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

        mock_db = AsyncMock()
        mock_db.fetchrow = AsyncMock(return_value=None)  # wrong org → no match

        with pytest.raises(HTTPException) as exc_info:
            await resolve_connection(auth, client_id, mock_db)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Pipelines v3 test (item 2)
# ---------------------------------------------------------------------------


class TestPipelinesV3:
    @pytest.mark.asyncio
    async def test_list_pipelines_uses_v3(self):
        client = HubSpotClient("conn-1")

        with patch.object(client, "_request", new_callable=AsyncMock, return_value={"results": []}) as mock_req:
            await client.list_pipelines("deals")

        mock_req.assert_called_once_with("GET", "/crm/v3/pipelines/deals")
