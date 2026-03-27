# hubspot-engine-x — Project Status & Roadmap

**Last updated:** 2026-03-27
**Current branch:** `bencrane/prod-readiness-audit`
**Deployment:** Not yet deployed (Railway configured, auto-deploys on push to `main`)

---

## Executive Summary

hubspot-engine-x is a multi-tenant API service that gives organizations programmatic control over their clients' HubSpot portals — connect, read, deploy, push, and remove — all through API, without ever logging into HubSpot.

**Build progress: ~76% complete (34 of ~45 target endpoints).**

- Phases 1-3 (foundation, org management, OAuth connections): **Done**
- Phase 4 (CRM read operations — search, list, get, batch-read, associations, pipelines, lists): **Done**
- Phase 5 (field mappings + push): **Done**
- Phases 6-7 (topology, conflicts, deploy, workflows): **Not started**

The implemented code is production-quality with proper auth, multi-tenancy enforcement, structured logging, CORS, shared HTTP client pooling, push idempotency, and 98 passing tests.

---

## What Has Been Built

### Implemented Endpoints (34)

| Group | Endpoints | Count |
|-------|-----------|-------|
| Super-Admin | POST /orgs, POST /users | 2 |
| Auth | POST /login, GET /me | 2 |
| Clients | POST /create, /list, /get | 3 |
| Users | POST /create, /list | 2 |
| API Tokens | POST /create, /list, /revoke | 3 |
| Connections | POST /create, /callback, /list, /get, /refresh, /revoke | 6 |
| CRM Read | POST /search, /list, /get, /batch-read, /associations, /associations/batch, /pipelines, /lists, /lists/members | 9 |
| Field Mappings | POST /set, /get, /list, /delete | 4 |
| Push | POST /records, /update, /link | 3 |
| Health | GET /health | 1 |

### Services

| Service | File | Status |
|---------|------|--------|
| HubSpot client | `app/services/hubspot.py` | Complete — CRM read, batch write, rate limiting, token caching, retry |
| Token manager | `app/services/token_manager.py` | Complete — Nango API client, shared httpx support |
| Push service | `app/services/push_service.py` | Complete — batch upsert/update/associations, field mapping, idempotency, logging |

### Infrastructure

| Component | Status |
|-----------|--------|
| Shared httpx.AsyncClient | Done — created in lifespan, injectable |
| CORS middleware | Done — configurable via ALLOWED_ORIGINS env var |
| Structured JSON logging | Done — python-json-logger, correlation IDs, audit log |
| DB pool tuning | Done — min_size=5, max_size=20, command_timeout=30 |
| Pagination safety | Done — max_pages=50 cap on _fetch_all |
| Push idempotency | Done — idempotency_key on push requests, dedup via crm_push_logs |
| Payload validation | Done — 3MB batch limit |

### Tests (98 passing)

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_auth.py` | 19 | Auth (API token, JWT, super-admin), RBAC, client access, multi-tenancy |
| `tests/test_endpoints.py` | 30 | All endpoint groups, happy path + auth denied |
| `tests/test_hubspot_service.py` | 15 | Error parsing, token caching, pagination, max_pages, resolve_connection, pipelines v3 |
| `tests/test_push_service.py` | 20 | Field mapping, batch helper, associations, idempotency, payload validation |
| `tests/test_crm_router.py` | 6 | CRM record transformation, association parsing |
| `tests/conftest.py` | — | Shared fixtures |

---

## What Has NOT Been Built

### Topology Pull + Snapshots (3 endpoints)
- `POST /api/topology/pull` — Pull client's full CRM schema
- `POST /api/topology/get` — Retrieve stored snapshot
- `POST /api/topology/history` — List snapshot versions

### Conflict Detection (2 endpoints)
- `POST /api/conflicts/check` — Pre-deploy conflict analysis
- `POST /api/conflicts/get` — Retrieve conflict report

### Deploy + Rollback (3 endpoints)
- `POST /api/deploy/custom-objects` — Create/update custom objects
- `POST /api/deploy/status` — Check deployment status
- `POST /api/deploy/rollback` — Remove deployed objects

### Workflows (3 endpoints)
- `POST /api/workflows/list` — List active workflows
- `POST /api/workflows/deploy` — Create/update automation rules
- `POST /api/workflows/remove` — Delete automations

### Missing Services
- `app/services/conflict_checker.py` — Pre-deploy validation
- `app/services/deploy_service.py` — Custom object/property deployment

---

## Open Gaps

| Gap | Severity | Detail |
|-----|----------|--------|
| **No RLS policies** | Medium | Tables have ENABLE ROW LEVEL SECURITY but no policies. App-layer + triggers provide isolation. |
| **No daily rate limit tracking** | Low | HubSpot 250k/day limit. In-memory counter viable for single container. |

---

## Codebase Stats

| Metric | Count |
|--------|-------|
| Implemented endpoints | 34 + health |
| Remaining endpoints | 11 |
| Routers | 9 |
| Services | 3 |
| Model files | 9 |
| Database tables | 10 |
| Test files | 5 |
| Total test cases | 98 |
