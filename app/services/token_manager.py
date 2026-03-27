import httpx

from app.config import settings


class NangoAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"Nango API error ({status_code}): {code} — {message}")


def _auth_headers(include_content_type: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {settings.NANGO_SECRET_KEY}"}
    if include_content_type:
        headers["Content-Type"] = "application/json"
    return headers


def _raise_nango_api_error(exc: httpx.HTTPStatusError) -> None:
    response = exc.response
    code = "unknown"
    message = response.text

    try:
        error_payload = response.json().get("error", {})
        if isinstance(error_payload, dict):
            code = str(error_payload.get("code", "unknown"))
            message = str(error_payload.get("message", response.text))
    except Exception:
        pass

    raise NangoAPIError(status_code=response.status_code, code=code, message=message) from None


async def create_connect_session(
    connection_id: str,
    org_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict:
    url = f"{settings.NANGO_BASE_URL}/connect/sessions"
    payload = {
        "tags": {
            "end_user_id": connection_id,
            "organization_id": org_id,
        },
        "allowed_integrations": [settings.NANGO_PROVIDER_CONFIG_KEY],
    }

    async def _do(client: httpx.AsyncClient) -> dict:
        response = await client.post(
            url,
            json=payload,
            headers=_auth_headers(include_content_type=True),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_nango_api_error(exc)
        return dict(response.json().get("data", {}))

    if http_client is not None:
        return await _do(http_client)
    async with httpx.AsyncClient() as client:
        return await _do(client)


async def get_connection(
    connection_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict:
    url = f"{settings.NANGO_BASE_URL}/connections/{connection_id}"
    params = {"provider_config_key": settings.NANGO_PROVIDER_CONFIG_KEY}

    async def _do(client: httpx.AsyncClient) -> dict:
        response = await client.get(
            url,
            params=params,
            headers=_auth_headers(),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_nango_api_error(exc)
        return dict(response.json())

    if http_client is not None:
        return await _do(http_client)
    async with httpx.AsyncClient() as client:
        return await _do(client)


async def get_valid_token(
    connection_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    connection = await get_connection(connection_id, http_client=http_client)
    credentials = connection.get("credentials", {})
    access_token = credentials.get("access_token")
    if not access_token:
        raise NangoAPIError(
            status_code=502,
            code="invalid_response",
            message="Nango response missing access token",
        )
    return str(access_token)


async def delete_connection(
    connection_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    url = f"{settings.NANGO_BASE_URL}/connections/{connection_id}"
    params = {"provider_config_key": settings.NANGO_PROVIDER_CONFIG_KEY}

    async def _do(client: httpx.AsyncClient) -> None:
        response = await client.delete(
            url,
            params=params,
            headers=_auth_headers(),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            _raise_nango_api_error(exc)

    if http_client is not None:
        await _do(http_client)
    else:
        async with httpx.AsyncClient() as client:
            await _do(client)
