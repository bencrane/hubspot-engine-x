# hubspot-engine-x — Project Status & Roadmap

Comprehensive assessment of what has been built, how it works, open gaps, and what remains.

**Last updated:** 2026-03-25
**Current branch:** `bencrane/melbourne`
**Deployment:** Not yet deployed (Railway configured, auto-deploys on push to `main`)

---

## Executive Summary

hubspot-engine-x is a multi-tenant API service that gives organizations programmatic control over their clients' HubSpot portals — connect, read, deploy, push, and remove — all through API, without ever logging into HubSpot.

**Build progress: ~50% complete by endpoint count.**

- Phases 1-3 (foundation, org management, OAuth connections): **Done**
- Phases 4-7 (CRM operations — topology, conflicts, deploy, push, workflows): **Not started**

The implemented code is production-quality with proper auth, multi-tenancy enforcement, and security practices. The remaining work is the HubSpot-specific CRM operations that make the service useful.

---

## What Has Been Built

### Phase 1: Foundation (complete)

| Component | File | What it does |
|-----------|------|-------------|
| Config | `app/config.py` | Pydantic Settings loading 9 env vars (DATABASE_URL, JWT_SECRET, SUPER_ADMIN_JWT_SECRET, HUBSPOT_CLIENT_ID/SECRET, NANGO_SECRET_KEY, NANGO_BASE_URL, NANGO_PROVIDER_CONFIG_KEY) |
| DB pool | `app/db.py` | asyncpg connection pool — init in lifespan, close on shutdown, yield per-request |
| Auth context | `app/auth/context.py` | `AuthContext` dataclass + `ROLE_PERMISSIONS` mapping 3 roles to 8 permissions |
| Auth dependencies | `app/auth/dependencies.py` | `get_current_auth()` — resolves super-admin, API token, or JWT into unified AuthContext. `require_permission()` factory, `validate_client_access()` helper |
| Password hashing | `app/auth/passwords.py` | Direct bcrypt (not passlib) — `hash_password()`, `verify_password()` |
| App shell | `app/main.py` | FastAPI app with lifespan, 6 routers mounted, `GET /health` with DB connectivity check |
| Deployment | `Dockerfile`, `railway.toml` | Python 3.13-slim, Doppler CLI for secrets injection, uvicorn, Railway healthcheck at `/health` |

**Commits:** `b6de2f9` through `c31e044` (6 commits)

### Phase 2: Auth + Org Management (complete — 12 endpoints)

| Router | Endpoints | Key behavior |
|--------|-----------|-------------|
| **Super-Admin** (`/api/super-admin`) | `POST /orgs`, `POST /users` | Bootstrap-only. Constant-time bearer auth against SUPER_ADMIN_JWT_SECRET. Creates orgs (name + unique slug) and users in any org. |
| **Auth** (`/api/auth`) | `POST /login`, `GET /me` | Login returns JWT (HS256, 24h expiry) with org_id/user_id/role/client_id. `/me` returns current AuthContext. |
| **Clients** (`/api/clients`) | `POST /create`, `POST /list`, `POST /get` | CRUD for an org's client companies. Requires `org.manage` for create. Unique constraint on (org_id, domain). |
| **Users** (`/api/users`) | `POST /create`, `POST /list` | User management within an org. Role + client_id validation (company roles require client_id, org_admin forbids it). bcrypt password hashing. |
| **API Tokens** (`/api/tokens`) | `POST /create`, `POST /list`, `POST /revoke` | Machine-to-machine tokens. Raw token returned once on create, SHA-256 hash stored. Soft-revoke via `is_active = FALSE`. `last_used_at` telemetry. |

**Commits:** `ab2412a` through `8beb4a6` (6 commits)

### Phase 3: OAuth Connections via Nango (complete — 6 endpoints)

| Component | What it does |
|-----------|-------------|
| **Token Manager** (`app/services/token_manager.py`) | Nango API client — `create_connect_session()`, `get_connection()`, `get_valid_token()`, `delete_connection()`. Structured `NangoAPIError` exception. |
| **Connections Router** (`/api/connections`) | Full OAuth lifecycle: create (initiate Nango connect session) → callback (confirm + store metadata) → list → get → refresh (force token refresh, marks expired on 424) → revoke (delete from Nango, mark revoked) |

**Connection metadata stored:** `hub_domain`, `hubspot_portal_id`, `scopes`, `nango_connection_id`, `status`. Tokens never touch our DB.

**Commits:** `99d6e66` through `20dd884` (3 commits)

### Health Check

`GET /health` — tests DB connectivity with `SELECT 1`, returns 200 or 503.

---

## How It Works

### Multi-Tenancy

Three-tier isolation: Organization → Client → User.

```
Revenue Activation (org)
├── Acme Corp (client) ─── connected HubSpot portal
├── Beta Inc (client) ─── connected HubSpot portal
└── Gamma LLC (client) ─── pending connection
```

**Every database query filters by `org_id`.** Child tables denormalize `org_id` to avoid joins. Database triggers enforce that `client_id` belongs to `org_id` on every insert/update — cross-tenant data leaks are impossible at the DB level.

### Authentication Flow

Three auth methods, one `AuthContext` output:

1. **Super-Admin** — shared secret bearer token, constant-time comparison, no DB lookup. Bootstrap only.
2. **API Tokens** — SHA-256 hash lookup in `api_tokens` table. Both token and user must be active. Used by data-engine-x, Trigger.dev, external integrations.
3. **JWT Sessions** — HS256, 24h expiry, issued on login. Contains org_id, user_id, role, client_id. Used by frontends.

All three produce the same `AuthContext(org_id, user_id, role, permissions, client_id, auth_method)` injected into every endpoint.

### RBAC

| Role | Scope | Key permissions |
|------|-------|----------------|
| `org_admin` | Org-wide | All 8 permissions — connect, deploy, push, manage |
| `company_admin` | Single client | connections.read, topology.read, workflows.read |
| `company_member` | Single client | connections.read, topology.read (read-only) |

### OAuth Connection Lifecycle

```
org_admin calls POST /connections/create
  → Nango creates connect session
  → Returns connect_link for frontend

User completes HubSpot OAuth in browser via Nango Connect UI

org_admin calls POST /connections/callback
  → Nango provides connection data
  → We extract hub_domain, portal_id, scopes
  → Store metadata, mark status='connected'

On every HubSpot API call:
  → token_manager.get_valid_token(connection_id)
  → Nango auto-refreshes if expired
  → Access token exists only in memory for the duration of the call
```

### Service Layer Boundary

All external API calls are isolated:
- **HubSpot CRM API** → `app/services/hubspot.py` (not yet created)
- **Nango API** → `app/services/token_manager.py` (complete)

No router imports `httpx`. No router calls external APIs directly.

---

## Database Schema

**10 tables** defined in `001_initial_schema.sql`, all with:
- UUID primary keys
- `org_id` NOT NULL + FK + index on every tenant-scoped table
- `created_at`/`updated_at` timestamps with auto-update triggers
- Tenant integrity triggers (client_id must belong to org_id)
- Row Level Security enabled (but **no policies defined yet**)

| Table | Status | Purpose |
|-------|--------|---------|
| `organizations` | In use | Tenant orgs |
| `clients` | In use | Org's customer companies |
| `users` | In use | People at the org |
| `api_tokens` | In use | Machine-to-machine auth (SHA-256 hashed) |
| `crm_connections` | In use | OAuth connection metadata (no tokens stored) |
| `crm_topology_snapshots` | Schema only | Versioned CRM schema snapshots (JSONB) |
| `crm_conflict_reports` | Schema only | Pre-deploy conflict analysis results |
| `crm_deployments` | Schema only | Deployment audit trail |
| `crm_push_logs` | Schema only | Record push history |
| `crm_field_mappings` | Schema only | Canonical-to-HubSpot property mapping |

**Enums:** user_role, connection_status, deployment_status, deployment_type, conflict_severity, push_status

---

## What Has NOT Been Built

### Phase 4: Topology Pull + Snapshots (3 endpoints)

| Endpoint | What it does |
|----------|-------------|
| `POST /api/topology/pull` | Pull client's full CRM schema from HubSpot (objects, properties, associations, pipelines) and store as versioned JSONB snapshot |
| `POST /api/topology/get` | Retrieve latest (or specific version) stored snapshot |
| `POST /api/topology/history` | List snapshot versions (metadata only, no JSONB payload) |

**Requires:** `app/services/hubspot.py` (HubSpot CRM API client — the most critical missing service)

**HubSpot APIs involved:**
- `GET /crm/v3/schemas` — custom object definitions
- `GET /crm/v3/properties/{objectType}` — properties per object
- `GET /crm/v4/associations/{from}/{to}/labels` — association types
- `GET /crm/v1/pipelines/{objectType}` — deal/ticket pipelines

### Phase 5A: Conflict Detection (2 endpoints)

| Endpoint | What it does |
|----------|-------------|
| `POST /api/conflicts/check` | Run pre-deploy conflict analysis against a client's current topology |
| `POST /api/conflicts/get` | Retrieve a specific conflict report |

**Requires:** `app/services/conflict_checker.py`, topology snapshots working first

**What gets checked:**
- Object name collisions (red) — custom object already exists
- Property name collisions (yellow) — property already exists on target object
- Required properties on standard objects (red) — required fields you won't populate
- Active workflows (yellow) — automations that fire on target objects
- Pipeline requirements (yellow) — object requires pipeline stage not specified

### Phase 5B: Deploy + Rollback (3 endpoints)

| Endpoint | What it does |
|----------|-------------|
| `POST /api/deploy/custom-objects` | Create/update custom objects and properties in client's HubSpot |
| `POST /api/deploy/status` | Check deployment status |
| `POST /api/deploy/rollback` | Remove deployed objects/properties |

**Requires:** `app/services/deploy_service.py`

**Key design:** Deployment plans are JSONB data (stored, diffed, rolled back), not code. The deploy service interprets them. This is significantly simpler than sfdc-engine-x because HubSpot uses synchronous REST/JSON for all schema operations — no Metadata API, no XML, no ZIP packaging, no async polling.

### Phase 6A: Field Mapping CRUD (4 endpoints)

| Endpoint | What it does |
|----------|-------------|
| `POST /api/field-mappings/set` | Create or update a canonical-to-HubSpot property mapping |
| `POST /api/field-mappings/get` | Get mappings for a client + object type |
| `POST /api/field-mappings/list` | List all mappings for a client |
| `POST /api/field-mappings/delete` | Remove a field mapping |

**Key rule:** HubSpot property names are never hardcoded in service logic. The push service reads `crm_field_mappings` at runtime. Different clients may have different property names, custom objects, or option values.

### Phase 6B: Push — Record Upserts (3 endpoints)

| Endpoint | What it does |
|----------|-------------|
| `POST /api/push/records` | Upsert records into client's HubSpot via Batch API |
| `POST /api/push/status-update` | Update property values on existing records |
| `POST /api/push/link` | Create associations between records |

**Requires:** `app/services/push_service.py`, field mappings working first

**HubSpot constraint:** Batch API allows 100 records per call. Rate limit: 100 requests per 10 seconds for OAuth apps.

### Phase 7: Workflows (3 endpoints)

| Endpoint | What it does |
|----------|-------------|
| `POST /api/workflows/list` | List active workflows in client's HubSpot |
| `POST /api/workflows/deploy` | Create/update automation rules |
| `POST /api/workflows/remove` | Delete deployed automations |

**Reduced scope vs sfdc-engine-x:** HubSpot workflows are primarily UI-built. The API supports listing and limited management, but not full programmatic creation of complex workflows like Salesforce Flows via Metadata API.

---

## Missing Services

| Service | File | Status | Purpose |
|---------|------|--------|---------|
| HubSpot client | `app/services/hubspot.py` | Not started | All HubSpot CRM API calls — schemas, properties, associations, batch ops, workflows |
| Conflict checker | `app/services/conflict_checker.py` | Not started | Pre-deploy conflict analysis against stored topology |
| Deploy service | `app/services/deploy_service.py` | Not started | Custom object/property creation, rollback |
| Push service | `app/services/push_service.py` | Not started | Batch API record upserts with field mapping resolution |

---

## Open Gaps & Risks

### Security

| Gap | Severity | Detail |
|-----|----------|--------|
| **No RLS policies** | Medium | All 10 tables have `ENABLE ROW LEVEL SECURITY` but zero policies defined. Currently relying entirely on application-level org_id filtering. If any query accidentally omits the filter, data leaks. |
| **No rate limiting** | Medium | No request rate limiting on our API. A compromised API token could hammer HubSpot's rate limits (100/10s, 500k/day) and exhaust quota for all clients in that org's HubSpot app. |
| **No request logging** | Low | No structured logging, no request tracing, no correlation IDs. Debugging production issues will be manual. |

### Testing

| Gap | Severity | Detail |
|-----|----------|--------|
| **Zero tests** | High | No test files, no test fixtures, no pytest in requirements.txt. 18 endpoints with auth, multi-tenancy, and external API integration — all untested. |

### Operational

| Gap | Severity | Detail |
|-----|----------|--------|
| **No HubSpot rate limit handling** | Medium | `hubspot.py` doesn't exist yet, but when built it needs retry logic with `Retry-After` header parsing for 429 responses. |
| **No connection pool tuning** | Low | asyncpg pool uses defaults. May need `min_size`/`max_size` configuration for production load. |
| **No API versioning** | Low | No `/v1/` prefix or versioning strategy. Breaking changes would require coordination with all consumers. |
| **No graceful degradation** | Low | If Nango is down, all connection operations fail with 502. No circuit breaker or cached state. |

### Documentation Drift

The existing docs (`SYSTEM_OVERVIEW.md`, `CHIEF_AGENT_DIRECTIVE.md`) still show all phases as "not started" — they haven't been updated since Phases 1-3 were completed. `CLAUDE.md` describes the full target state including files that don't exist yet (e.g., `app/services/hubspot.py`, `app/routers/topology.py`).

---

## Dependency Graph for Remaining Work

```
Phase 4: Topology ──────────┐
  requires: hubspot.py       │
                              ▼
Phase 5A: Conflicts ────► Phase 5B: Deploy
  requires: topology          requires: hubspot.py
  requires: conflict_checker  requires: deploy_service
                              │
Phase 6A: Field Mappings      │  (independent — can parallel with 5)
  requires: DB only           │
         │                    │
         ▼                    ▼
Phase 6B: Push ◄──────────────┘
  requires: hubspot.py
  requires: push_service
  requires: field_mappings working

Phase 7: Workflows (independent — can parallel with 6)
  requires: hubspot.py
```

**Critical path:** `hubspot.py` → topology → conflicts → deploy → push

**Parallelizable:** Field mappings (6A) can be built any time. Workflows (7) can start once `hubspot.py` exists.

---

## Codebase Stats

| Metric | Count |
|--------|-------|
| Total Python LOC | ~1,516 |
| Implemented endpoints | 19 (18 + health) |
| Remaining endpoints | 18 |
| Routers implemented | 6 of 12 |
| Services implemented | 1 of 5 |
| Models files | 6 (admin, auth, clients, connections, tokens, users) |
| Database tables | 10 (5 in active use, 5 schema-only) |
| Test files | 0 |
| Commits | 16 |

---

## Roadmap: What to Build Next

### Immediate (Phase 4)

1. **Build `app/services/hubspot.py`** — the HubSpot CRM API client. This is the single most important missing piece. Every remaining phase depends on it. Should include:
   - Schema operations (list/get custom objects)
   - Property operations (list/get/create/update properties per object)
   - Association type listing
   - Pipeline listing
   - Rate limit retry with `Retry-After` header
   - Structured error handling preserving HubSpot correlation IDs

2. **Build topology router + models** — `POST /topology/pull`, `/get`, `/history`. This proves the HubSpot integration end-to-end.

### Short-term (Phases 5-6)

3. **Conflict checker + deploy service** — pre-deploy validation and synchronous custom object/property creation.
4. **Field mapping CRUD** — simple DB operations, can be built in parallel.
5. **Push service** — batch record upserts with field mapping resolution.

### Medium-term

6. **Workflows** — reduced scope, listing + limited management.
7. **Tests** — at minimum: auth dependency tests, multi-tenancy isolation tests, HubSpot service tests with mocked responses.
8. **RLS policies** — defense in depth for tenant isolation at the DB level.
9. **Structured logging + request tracing** — correlation IDs for debugging.
10. **Rate limiting** — protect HubSpot API quotas from runaway consumers.

### Not Planned (but worth noting)

- **Webhook ingestion** — HubSpot can send webhooks on CRM events. Not in scope but would enable real-time sync.
- **Bulk import API** — HubSpot has a separate imports API for large data loads. Current design uses Batch API (100 records/call).
- **Custom behavioral events** — HubSpot analytics events API. Not in current scope.
- **Marketing API integration** — email, forms, landing pages. Out of scope (CRM only).

---

## Agent Development Model

This project uses a chief/executor agent pattern:

- **Chief agent** reads context, makes architectural decisions, writes directives, reviews work, pushes to main
- **Executor agents** receive detailed directives and implement one deliverable per commit
- **Strategic directive** (`STRATEGIC_DIRECTIVE.md`) defines 15 non-negotiable build rules
- **Directive template** (`WRITING_EXECUTOR_DIRECTIVES.md`) standardizes how implementation instructions are written

Key rules from the strategic directive:
1. Service layer boundary — no httpx in routers
2. Every query filters by org_id
3. Tokens never leave the service layer
4. One commit per deliverable
5. No deploy without review
6. Deployment plans are JSONB data, not code
7. Field mappings are per-client (no hardcoded property names)
8. Errors preserve HubSpot context (correlation IDs, original messages)
9. Soft delete everything
10. Topology before deploy (convention, not enforcement)
