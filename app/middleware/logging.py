"""Structured logging middleware with correlation IDs and audit logging."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("hubspot_engine_x")

# Paths that trigger audit log entries (sensitive operations)
_AUDIT_PATHS = frozenset({
    "/api/tokens/create",
    "/api/tokens/revoke",
    "/api/connections/revoke",
    "/api/push/records",
    "/api/push/update",
    "/api/push/link",
})


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        start = time.monotonic()

        response = await call_next(request)

        duration_ms = round((time.monotonic() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id

        # Extract auth info from request state if available
        org_id = getattr(request.state, "org_id", None) if hasattr(request, "state") else None
        user_id = getattr(request.state, "user_id", None) if hasattr(request, "state") else None

        log_data = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        }
        if org_id:
            log_data["org_id"] = org_id
        if user_id:
            log_data["user_id"] = user_id

        logger.info("request", extra=log_data)

        # Audit log for sensitive operations
        if request.url.path in _AUDIT_PATHS:
            logger.info(
                "audit",
                extra={
                    **log_data,
                    "audit": True,
                },
            )

        return response
