"""HubSpot CRM API client.

All HubSpot API calls go through this module. No router calls HubSpot directly.
Tokens are obtained from Nango via token_manager and never leave this service layer.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator
from typing import Any

import asyncpg
import httpx
from fastapi import HTTPException, status

from app.auth.context import AuthContext
from app.services import token_manager
from app.services.token_manager import NangoAPIError

HUBSPOT_BASE_URL = "https://api.hubapi.com"

# ---------------------------------------------------------------------------
# Rate-limit tracking (in-memory, per-portal / connection_id)
# ---------------------------------------------------------------------------
# HubSpot allows 100 requests per 10 seconds for OAuth apps.
# We use 95 as a buffer. This is per-process — acceptable for a single
# Railway container. Multi-worker deployments would need Redis.
_rate_tracker: dict[str, deque[float]] = {}
_WINDOW_SECONDS = 10
_MAX_PER_WINDOW = 95
_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HubSpotAPIError(Exception):
    """Structured error from HubSpot preserving correlationId and category."""

    def __init__(
        self,
        status_code: int,
        category: str,
        message: str,
        correlation_id: str | None = None,
    ):
        self.status_code = status_code
        self.category = category
        self.message = message
        self.correlation_id = correlation_id
        super().__init__(f"HubSpot API error ({status_code}): {category} — {message}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_hubspot_error(response: httpx.Response) -> HubSpotAPIError:
    """Extract structured error fields from a HubSpot error response."""
    category = "UNKNOWN"
    message = response.text
    correlation_id: str | None = None

    try:
        body = response.json()
        category = str(body.get("category", "UNKNOWN"))
        message = str(body.get("message", response.text))
        correlation_id = body.get("correlationId")
    except Exception:
        pass

    return HubSpotAPIError(
        status_code=response.status_code,
        category=category,
        message=message,
        correlation_id=correlation_id,
    )


async def _wait_for_rate_limit(connection_id: str) -> None:
    """Block until the sliding window has capacity for one more request."""
    tracker = _rate_tracker.setdefault(connection_id, deque())
    now = time.monotonic()

    # Prune timestamps older than the window
    while tracker and tracker[0] < now - _WINDOW_SECONDS:
        tracker.popleft()

    if len(tracker) >= _MAX_PER_WINDOW:
        sleep_for = tracker[0] + _WINDOW_SECONDS - now
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        # Re-prune after sleeping
        now = time.monotonic()
        while tracker and tracker[0] < now - _WINDOW_SECONDS:
            tracker.popleft()


def _record_request(connection_id: str) -> None:
    """Record a request timestamp for rate-limit tracking."""
    _rate_tracker.setdefault(connection_id, deque()).append(time.monotonic())


# ---------------------------------------------------------------------------
# Connection resolver (shared by CRM and push routers)
# ---------------------------------------------------------------------------


async def resolve_connection(
    auth: AuthContext, client_id: uuid.UUID, db: asyncpg.Connection
) -> str:
    """Look up the active Nango connection ID for a client.

    Returns the ``nango_connection_id`` string.
    Raises 404 if no connected connection exists for the client + org.
    """
    org_id = uuid.UUID(auth.org_id)
    row = await db.fetchrow(
        """
        SELECT nango_connection_id
        FROM crm_connections
        WHERE client_id = $1 AND org_id = $2 AND status = 'connected'
        """,
        client_id,
        org_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active connection for this client",
        )
    return str(row["nango_connection_id"])


# ---------------------------------------------------------------------------
# HubSpot client
# ---------------------------------------------------------------------------


class HubSpotClient:
    """Per-request HubSpot API client scoped to a single connection (portal).

    Usage in routers::

        connection_id = await resolve_connection(auth, client_id, db)
        hs = HubSpotClient(connection_id)
        data = await hs.list_objects("contacts", properties=["email"])
    """

    def __init__(self, connection_id: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._connection_id = connection_id
        self._cached_token: str | None = None
        self._http_client = http_client

    # -- Token management ---------------------------------------------------

    async def _get_token(self) -> str:
        if self._cached_token is None:
            try:
                self._cached_token = await token_manager.get_valid_token(
                    self._connection_id
                )
            except NangoAPIError as exc:
                raise HubSpotAPIError(
                    status_code=502,
                    category="TOKEN_ERROR",
                    message=f"Failed to obtain HubSpot token: {exc.message}",
                ) from None
        return self._cached_token

    def _clear_token_cache(self) -> None:
        self._cached_token = None

    # -- Core request -------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to HubSpot with rate-limit and retry logic."""
        url = f"{HUBSPOT_BASE_URL}{path}"
        retries_left = _MAX_RETRIES
        token_retried = False

        while True:
            await _wait_for_rate_limit(self._connection_id)
            token = await self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            if self._http_client is not None:
                response = await self._http_client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=30.0,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        headers=headers,
                        timeout=30.0,
                    )

            _record_request(self._connection_id)

            if response.status_code == 429 and retries_left > 0:
                retry_after = int(response.headers.get("Retry-After", "10"))
                await asyncio.sleep(retry_after)
                retries_left -= 1
                continue

            if response.status_code == 401 and not token_retried:
                self._clear_token_cache()
                token_retried = True
                continue

            if response.status_code >= 400:
                raise _parse_hubspot_error(response)

            return dict(response.json()) if response.text else {}

    # -- Pagination ---------------------------------------------------------

    async def _paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int = 50,
    ) -> AsyncGenerator[list[dict[str, Any]], None]:
        """Yield pages of results following HubSpot's ``paging.next.after`` cursor."""
        params = dict(params or {})
        pages_fetched = 0
        while True:
            data = await self._request("GET", path, params=params)
            results = data.get("results", [])
            if results:
                yield results
            pages_fetched += 1
            next_after = (
                data.get("paging", {}).get("next", {}).get("after")
            )
            if not next_after or pages_fetched >= max_pages:
                break
            params["after"] = next_after

    async def _fetch_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Collect all pages into a flat list (capped at max_pages)."""
        all_results: list[dict[str, Any]] = []
        async for page in self._paginate(path, params, max_pages=max_pages):
            all_results.extend(page)
        return all_results

    # -- CRM Objects --------------------------------------------------------

    async def list_objects(
        self,
        object_type: str,
        *,
        properties: list[str] | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if properties:
            params["properties"] = ",".join(properties)
        if after:
            params["after"] = after
        return await self._request("GET", f"/crm/v3/objects/{object_type}", params=params)

    async def get_object(
        self,
        object_type: str,
        object_id: str,
        *,
        properties: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if properties:
            params["properties"] = ",".join(properties)
        return await self._request(
            "GET", f"/crm/v3/objects/{object_type}/{object_id}", params=params
        )

    async def search_objects(
        self,
        object_type: str,
        *,
        filter_groups: list[dict[str, Any]] | None = None,
        properties: list[str] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"limit": limit}
        if filter_groups:
            body["filterGroups"] = filter_groups
        if properties:
            body["properties"] = properties
        if sorts:
            body["sorts"] = sorts
        if after:
            body["after"] = after
        return await self._request(
            "POST", f"/crm/v3/objects/{object_type}/search", json_body=body
        )

    async def batch_read(
        self,
        object_type: str,
        ids: list[str],
        *,
        properties: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"inputs": [{"id": id_} for id_ in ids]}
        if properties:
            body["properties"] = properties
        return await self._request(
            "POST", f"/crm/v3/objects/{object_type}/batch/read", json_body=body
        )

    # -- Associations -------------------------------------------------------

    async def list_associations(
        self,
        from_object_type: str,
        to_object_type: str,
        object_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/crm/v3/objects/{from_object_type}/{object_id}/associations/{to_object_type}",
        )

    async def batch_read_associations(
        self,
        from_object_type: str,
        to_object_type: str,
        object_ids: list[str],
    ) -> dict[str, Any]:
        body = {"inputs": [{"id": id_} for id_ in object_ids]}
        return await self._request(
            "POST",
            f"/crm/v4/associations/{from_object_type}/{to_object_type}/batch/read",
            json_body=body,
        )

    # -- Pipelines ----------------------------------------------------------

    async def list_pipelines(self, object_type: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/crm/v3/pipelines/{object_type}")
        return list(data.get("results", []))

    # -- Lists --------------------------------------------------------------

    async def list_lists(
        self,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"count": limit}
        if after:
            params["after"] = after
        return await self._request("GET", "/crm/v3/lists", params=params)

    async def get_list_memberships(
        self,
        list_id: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        return await self._request(
            "GET", f"/crm/v3/lists/{list_id}/memberships", params=params
        )

    # -- Batch Write (used by push service) ---------------------------------

    async def batch_create(
        self, object_type: str, inputs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/crm/v3/objects/{object_type}/batch/create",
            json_body={"inputs": inputs},
        )

    async def batch_update(
        self, object_type: str, inputs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/crm/v3/objects/{object_type}/batch/update",
            json_body={"inputs": inputs},
        )

    async def batch_upsert(
        self, object_type: str, inputs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/crm/v3/objects/{object_type}/batch/upsert",
            json_body={"inputs": inputs},
        )

    async def batch_create_associations(
        self,
        from_object_type: str,
        to_object_type: str,
        inputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/crm/v4/associations/{from_object_type}/{to_object_type}/batch/create",
            json_body={"inputs": inputs},
        )
