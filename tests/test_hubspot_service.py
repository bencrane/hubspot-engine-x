"""Tests for the HubSpot API client (app/services/hubspot.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from app.services.hubspot import (
    HubSpotAPIError,
    HubSpotClient,
    _parse_hubspot_error,
    _record_request,
    _rate_tracker,
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
