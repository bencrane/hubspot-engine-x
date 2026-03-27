import logging

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pythonjsonlogger import json as json_log

from app import db
from app.config import settings
from app.middleware.logging import LoggingMiddleware
from app.routers import admin, auth, clients, connections, crm, field_mappings, push, tokens, users

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------

_log_handler = logging.StreamHandler()
_log_handler.setFormatter(
    json_log.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")
)
logging.getLogger("hubspot_engine_x").addHandler(_log_handler)
logging.getLogger("hubspot_engine_x").setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Shared httpx client (stored on app.state)
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    if _http_client is None:
        raise RuntimeError("HTTP client is not initialized")
    return _http_client


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _http_client
    await db.init_pool(settings.DATABASE_URL)
    _http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=30),
        timeout=httpx.Timeout(30.0),
    )
    try:
        yield
    finally:
        if _http_client is not None:
            await _http_client.aclose()
            _http_client = None
        await db.close_pool()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# CORS
_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Logging + correlation IDs
app.add_middleware(LoggingMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(connections.router)
app.include_router(users.router)
app.include_router(tokens.router)
app.include_router(crm.router)
app.include_router(field_mappings.router)
app.include_router(push.router)


@app.get("/health", response_model=None)
async def health() -> dict[str, str] | JSONResponse:
    try:
        pool = db.get_pool()
        async with pool.acquire() as connection:
            await connection.execute("SELECT 1")
        return {"status": "healthy"}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "detail": str(exc)},
        )
