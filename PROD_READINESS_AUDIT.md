# Production Readiness Audit — Completed 2026-03-27

**PR:** [#2](https://github.com/bencrane/hubspot-engine-x/pull/2)
**Branch:** `bencrane/prod-readiness-audit`
**Merged to main:** 2026-03-27

---

## Scope

18 implementation items + 1 research task across 4 groups: infrastructure hardening, behavioral fixes, comprehensive test coverage, and documentation. The goal was to close every gap between the existing codebase and production-ready deployment on Railway.

**Total diff:** 21 files changed, +2,028 / −494 lines.

---

## Group 1 — Infrastructure

### Item 1: Shared httpx.AsyncClient

**Files:** `app/main.py`, `app/services/hubspot.py`, `app/services/token_manager.py`

Created a single `httpx.AsyncClient` in the FastAPI lifespan with connection pooling:

```python
_http_client = httpx.AsyncClient(
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=30),
    timeout=httpx.Timeout(30.0),
)
```

- `get_http_client()` function provides injectable access
- `HubSpotClient.__init__` accepts optional `http_client` parameter — uses shared client if provided, falls back to per-request client
- All `token_manager.py` functions accept optional `http_client` parameter with same fallback pattern
- Client is properly closed in the lifespan `finally` block

### Item 3: CORS Middleware

**Files:** `app/config.py`, `app/main.py`

- Added `ALLOWED_ORIGINS: str = ""` to Settings (comma-separated)
- CORS middleware only mounted if origins are configured — no CORS headers when empty
- Supports `allow_credentials=True`, all methods, all headers

### Item 4: Structured JSON Logging + Correlation IDs

**Files:** `app/main.py`, `app/middleware/__init__.py`, `app/middleware/logging.py`, `requirements.txt`

- New `LoggingMiddleware` generates or propagates `X-Request-ID` on every request
- Logs: request_id, method, path, status, duration_ms, org_id, user_id
- Audit flag on sensitive paths: token create/revoke, connection revoke, all push endpoints
- JSON output via `python-json-logger` (added to requirements.txt)

### Item 5: DB Pool Tuning

**File:** `app/db.py`

```python
_pool = await asyncpg.create_pool(
    database_url,
    min_size=5,
    max_size=20,
    command_timeout=30,
)
```

Previously used asyncpg defaults (min_size=10, max_size=10, no command_timeout).

---

## Group 2 — Behavioral Changes

### Item 2: HubSpot Pipelines v1 → v3

**File:** `app/services/hubspot.py`

Changed `list_pipelines` from `/crm/v1/pipelines/{object_type}` to `/crm/v3/pipelines/{object_type}`. The v1 endpoint is deprecated; v3 returns the same structure with additional fields.

### Item 6: Pagination Max Pages Safety Cap

**File:** `app/services/hubspot.py`

`_paginate` and `_fetch_all` now accept `max_pages: int = 50`. Pagination stops after 50 pages regardless of `hasMore`, preventing runaway loops against large HubSpot portals.

### Item 7: Field Mapping Resolution in batch_update

**File:** `app/services/push_service.py`

`batch_update` now calls `resolve_field_mappings` and `apply_field_mappings` before sending to HubSpot, matching the behavior already present in `batch_upsert`. Previously, `batch_update` sent raw canonical field names.

### Item 8: DRY Batch Helper

**File:** `app/services/push_service.py`

New `_execute_batched_push(hs_callable, inputs, batch_size=100)` function:

- Chunks inputs into batch_size groups
- Calls the HubSpot callable per chunk
- Accumulates succeeded/failed/errors across chunks
- Catches `HubSpotAPIError` per batch — partial failures don't abort remaining batches
- Used by `batch_upsert`, `batch_update`, and `create_associations`

### Item 9: Remove Unused transform_rule

**Files:** `app/models/field_mappings.py`, `app/routers/field_mappings.py`

Removed `transform_rule: dict[str, Any] | None = None` from `SetFieldMappingRequest` and removed it from the INSERT/UPDATE SQL in the field mappings router. The field was defined but never used anywhere.

### Item 10: Payload Size Validation

**File:** `app/services/push_service.py`

New `_validate_payload_size(inputs)` function raises HTTP 400 if JSON-serialized payload exceeds 3MB. Called at the start of `batch_upsert`, `batch_update`, and `create_associations`.

### Item 11: Push Idempotency

**Files:** `app/models/push.py`, `app/routers/push.py`, `app/services/push_service.py`, `supabase/migrations/002_push_idempotency.sql`

- Added `idempotency_key: str | None = None` to `PushRecordsRequest`, `PushUpdateRequest`, `PushLinkRequest`
- New `_check_idempotency(db, org_id, idempotency_key)` — checks `crm_push_logs` for existing key
  - If found with status `succeeded` or `partial`: returns cached result with "Idempotent replay" warning
  - If found with status `failed`: returns `None` (allows retry)
  - If not found: returns `None` (new push)
- All three push service functions check idempotency before executing
- `log_push` stores `idempotency_key` in the push log
- Migration adds `idempotency_key TEXT` column + partial unique index on `(org_id, idempotency_key) WHERE idempotency_key IS NOT NULL`

### Item 12: token_manager Shared Client Support

**File:** `app/services/token_manager.py`

All functions (`get_access_token`, `create_connect_session`, `delete_connection`) accept optional `http_client: httpx.AsyncClient | None = None`. Uses shared client when provided, creates per-request client as fallback.

---

## Group 3 — Tests

### Item 13: Auth Tests — `tests/test_auth.py` (19 tests)

| Class | Tests | Coverage |
|-------|-------|----------|
| `TestSuperAdminAuth` | 3 | Valid token, wrong token, missing header |
| `TestAPITokenAuth` | 2 | Valid API token, inactive falls through to JWT |
| `TestJWTAuth` | 6 | Valid JWT, expired, missing claims, unknown role, wrong signing secret, with client_id |
| `TestRBAC` | 4 | Permission granted, denied, company_admin cannot push, can read topology |
| `TestClientAccess` | 4 | Client belongs to org, not found, wrong client, own client |

Key pattern: `_make_pool_with_row()` helper uses `@asynccontextmanager` to properly mock asyncpg pool's async context manager protocol.

### Item 14: Endpoint Tests — `tests/test_endpoints.py` (30 tests)

Uses FastAPI `dependency_overrides` to inject pre-built `AuthContext` objects, completely decoupling endpoint tests from the auth chain.

Four test client fixtures:
- `admin_client` — org_admin with full permissions
- `member_client` — company_member with read-only permissions
- `super_admin_client` — bypasses auth via super-admin override
- `noauth_client` — no auth injected (tests 401 paths)

| Class | Tests | Coverage |
|-------|-------|----------|
| `TestHealth` | 1 | Health endpoint returns 200 |
| `TestSuperAdmin` | 3 | Create org, create user, auth denied |
| `TestAuth` | 2 | Login, /me |
| `TestClients` | 3 | Create, list, auth denied |
| `TestUsers` | 3 | Create, list, auth denied |
| `TestTokens` | 4 | Create, list, revoke, auth denied |
| `TestConnections` | 4 | Create, list, get, auth denied |
| `TestCRM` | 4 | Search, list, get, auth denied |
| `TestFieldMappings` | 3 | Set, list, auth denied |
| `TestPush` | 3 | Records, update, auth denied |

### Item 15: HubSpot Service Tests — `tests/test_hubspot_service.py` (+8 tests, 15 total)

New test classes added:
- `TestPagination` (3): multi-page, empty response, max_pages limit enforcement
- `TestResolveConnection` (3): connection found, not found, wrong org
- `TestPipelinesV3` (1): verifies `/crm/v3/pipelines/` URL
- `test_request_with_shared_http_client` (1): verifies shared client is used when provided

### Item 16: Push Service Tests — `tests/test_push_service.py` (+12 tests, 20 total)

New test classes added:
- `TestExecuteBatchedPush` (4): single batch, multiple batches, API error handling, partial batch errors
- `TestCreateAssociations` (2): input shaping (from/to/types), batching at 100
- `TestPayloadValidation` (2): small payload passes, oversized raises with "3MB" message
- `TestIdempotency` (3): cache hit returns replay, cache miss returns None, failed status allows retry
- `TestBatchUpdateFieldMapping` (1): verifies field mapping applied before HubSpot call

---

## Group 4 — Documentation

### Item 17: PROJECT_STATUS.md and CLAUDE.md

- `PROJECT_STATUS.md` rewritten to reflect current state: 34 endpoints, 98 tests, 3 services, all infrastructure additions
- `CLAUDE.md` updated: directory structure matches actual files (middleware package, test files), `ALLOWED_ORIGINS` added to environment variables

### Item 18: .env.example

New file with all 10 environment variables, placeholder values, and comments explaining each.

### Item 19: Nango OAuth v3 Research

Researched Nango's HubSpot v3 API support. Confirmed Nango fully supports HubSpot v3 integration — no changes needed to Nango configuration or our token management layer.

---

## Migration Required

```sql
-- 002_push_idempotency.sql — run before deploying
ALTER TABLE crm_push_logs ADD COLUMN idempotency_key TEXT;

CREATE UNIQUE INDEX idx_push_logs_idempotency
    ON crm_push_logs (org_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
```

---

## Test Results

```
98 passed, 1 warning in 1.93s
```

The single warning is `InsecureKeyLengthWarning` from PyJWT for a 31-byte test secret — not applicable in production where secrets are 32+ bytes via Doppler.

---

## Remaining Open Items (Not In Scope)

These were identified during the audit but were not part of the implementation plan:

| Item | Severity | Notes |
|------|----------|-------|
| No RLS policies | Medium | Tables have `ENABLE ROW LEVEL SECURITY` but no policies. App-layer filtering + tenant integrity triggers provide isolation. |
| No daily rate limit tracking | Low | HubSpot 250k/day limit. In-memory counter viable for single container. |
| Phases 6-7 not started | — | Topology, conflicts, deploy, workflows — 11 endpoints + 2 services remaining |
