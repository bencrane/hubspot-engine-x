from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db
from app.config import settings
from app.routers import admin, auth, clients, tokens, users


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.init_pool(settings.DATABASE_URL)
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(users.router)
app.include_router(tokens.router)


@app.get("/health")
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
