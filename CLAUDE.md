# CLAUDE.md

Authoritative context for AI agents working in `hubspot-engine-x`.

---

## What This Is

hubspot-engine-x is a multi-tenant API service that provides programmatic HubSpot administration for client organizations. It is a remote control for clients' HubSpot portals — connect, read, deploy, push, and remove — all through API, without ever logging into a client's HubSpot account.

This is **not** a HubSpot app or marketplace integration. It is infrastructure that organizations (RevOps firms, agencies, service providers) use to manage their clients' HubSpot portals via API contracts.

## Who Uses It

- **Organizations (orgs):** RevOps firms, staffing agencies, service providers. Each org is a tenant.
- **Clients:** The org's customers whose HubSpot portals are being managed.
- **Users:** People at the org who interact with the system (org admins, company admins, operators).

Revenue Activation is the first org. Staffing Activation is a use case within RA. Other RevOps firms can onboard as separate orgs in the future.

## What It Does

| Capability | Description |
|-----------|-------------|
| **Connect** | OAuth flow — client authorizes, tokens managed by Nango |
| **Read** | Pull full CRM topology — objects, properties, associations, pipelines |
| **Deploy** | Create/update custom objects, properties, association types in client's HubSpot |
| **Push** | Upsert records, update properties, create associations |
| **Remove** | Clean up deployed objects/properties on client churn |

All operations are scoped by org_id and client_id. An org never sees another org's data. A client's connection is never accessible by another client.

---

## Stack

| Layer | Choice | Why |
|-------|--------|-----|
| API Framework | FastAPI (Python 3.13) | Async, Pydantic models, dependency injection for AuthContext |
| Deployment | Railway (Dockerfile) | Docker build, SSL, auto-deploy on push to main |
| Database | Supabase Postgres via asyncpg | Direct connection, async, no ORM overhead |
| Secrets | Doppler | Centralized secrets management, injected at runtime |
| Auth | API tokens (SHA-256 hash) + JWT (HS256) | Machine-to-machine (tokens) and user sessions (JWT) |
| OAuth | Nango | Manages HubSpot OAuth flow, token storage, automatic refresh |
| Password Hashing | bcrypt (direct) | No passlib — direct bcrypt library |
| HTTP Client | httpx | Async HTTP for HubSpot and Nango API calls |
| External API | HubSpot CRM API v3 | All CRM operations via stored OAuth tokens in Nango |

**No Modal.** This service handles straightforward request/response operations (OAuth exchanges, API calls to HubSpot, DB reads/writes). No serverless compute needed.

---

## Multi-Tenancy Model

### Three Tiers

```
Tier 1: Organization    — The business (RA, future RevOps firms)
Tier 2: Client           — Customer of that org (Acme Corp, etc.)
Tier 3: User             — Person at the org
```

### Key Rule

> **Every database query must filter by `org_id`.** No exceptions.

Company-level resources additionally filter by `client_id` after validating the client belongs to the org.

### Denormalization

Child tables include `org_id` even when they reference a parent that already has it. This avoids joins for tenant filtering and provides defense in depth.

---

## Auth Model

### Three Auth Methods

**Super-Admin** (bootstrap only):
- Bearer token matched against `SUPER_ADMIN_JWT_SECRET` via constant-time comparison (`hmac.compare_digest`)
- Used only for: org creation, first user creation
- No JWT, no DB lookup — the shared secret IS the token

**API Tokens** (machine-to-machine):
- SHA-256 hashed and stored in `api_tokens` table
- Looked up on each request → returns org_id, user_id, role
- Query enforces both `t.is_active = TRUE` and `u.is_active = TRUE`
- Used by: data-engine-x, trigger.dev tasks, external integrations

**JWT Sessions** (user login):
- Issued on login, signed with `JWT_SECRET` (HS256)
- Contains: `org_id`, `user_id`, `role`, `client_id`, `exp`
- `exp` claim is required — tokens without expiry are rejected
- Required claims validated: `org_id`, `user_id`, `role` must all be present
- Unknown roles (not in ROLE_PERMISSIONS) are rejected
- Used by: admin frontend, user-facing interfaces

### AuthContext

All three auth methods produce the same AuthContext object, injected into every endpoint via FastAPI dependency:

```python
@dataclass
class AuthContext:
    org_id: str
    user_id: str
    role: str              # org_admin, company_admin, company_member
    permissions: list[str] # derived from role via ROLE_PERMISSIONS
    client_id: str | None  # set for company-scoped users
    auth_method: str       # "api_token" or "session"
```

### RBAC

| Role | Scope |
|------|-------|
| `org_admin` | Full access — manage connections, deploy, push, manage users/clients |
| `company_admin` | Client-scoped — view connection status, view topology |
| `company_member` | Client-scoped — read-only |

### Permissions

```
connections.read, connections.write
topology.read
deploy.write
push.write
workflows.read, workflows.write
org.manage
```

---

## Database Tables

| Table | Purpose |
|-------|---------:|
| `organizations` | Tenant orgs |
| `clients` | Org's customers (the staffing agencies, etc.) |
| `users` | People at the org |
| `api_tokens` | Machine-to-machine auth tokens (SHA-256 hashed) |
| `crm_connections` | Connection metadata — status, hub_domain, hubspot_portal_id, nango_connection_id per client (no tokens stored) |
| `crm_topology_snapshots` | Full CRM schema snapshots (JSONB), versioned |
| `crm_deployments` | Log of what was deployed — objects, properties, when, to which client |
| `crm_conflict_reports` | Pre-deploy conflict check results |
| `crm_push_logs` | Record push history with success/fail counts |
| `crm_field_mappings` | Canonical-to-HubSpot property mapping per client per object |

All tenant-scoped tables have `org_id` with NOT NULL constraint, foreign key, index, and tenant integrity triggers.

---

## API Conventions

- **All endpoints use POST** (except `GET /health` and `GET /api/auth/me`) — parameters in request body as JSON
- **UUID fields in request bodies use Pydantic `UUID` type** — invalid UUIDs get 422 before reaching the database
- **AuthContext injected on every endpoint** via dependency
- **Every query scoped by org_id** at minimum
- **Thin endpoints** — validate, call HubSpot or DB, return
- **HubSpot errors surfaced as 502** with original error code and message preserved

### Error Codes

| Code | Meaning |
|------|---------:|
| 401 | Missing or invalid auth token |
| 403 | Valid token but insufficient permissions |
| 404 | Resource not found or belongs to different org |
| 400 | Invalid request payload |
| 422 | Invalid request format — Pydantic validation (e.g., malformed UUID) |
| 502 | HubSpot or Nango API error |

---

## API Endpoints

### Super-Admin (bootstrap)
- `POST /api/super-admin/orgs` — create an organization
- `POST /api/super-admin/users` — create a user in any org

### Auth
- `POST /api/auth/login` — issue JWT session token
- `GET /api/auth/me` — return current auth context with role and permissions

### Clients
- `POST /api/clients/create` — create a client for the org
- `POST /api/clients/list` — list clients for the org
- `POST /api/clients/get` — get client details

### Users
- `POST /api/users/create` — create a user in the org
- `POST /api/users/list` — list users in the org

### API Tokens
- `POST /api/tokens/create` — create API token (raw token returned once)
- `POST /api/tokens/list` — list tokens (never exposes token value)
- `POST /api/tokens/revoke` — soft-deactivate a token

### Connections
- `POST /api/connections/create` — initiate OAuth via Nango connect session
- `POST /api/connections/callback` — confirm connection after OAuth completes
- `POST /api/connections/list` — list connections for org (or specific client)
- `POST /api/connections/get` — get connection details and status
- `POST /api/connections/refresh` — force token refresh via Nango
- `POST /api/connections/revoke` — disconnect a client's HubSpot

### Topology
- `POST /api/topology/pull` — pull and store client's full CRM schema
- `POST /api/topology/get` — retrieve latest (or specific version) stored snapshot
- `POST /api/topology/history` — list snapshot versions (no JSONB payload)

### Conflicts
- `POST /api/conflicts/check` — run pre-deploy conflict analysis
- `POST /api/conflicts/get` — retrieve a specific conflict report

### Deploy
- `POST /api/deploy/custom-objects` — create/update custom objects and properties
- `POST /api/deploy/status` — check deployment status
- `POST /api/deploy/rollback` — remove deployed objects/properties

### Field Mappings
- `POST /api/field-mappings/set` — create or update a field mapping
- `POST /api/field-mappings/get` — get mappings for a client + object
- `POST /api/field-mappings/list` — list all mappings for a client
- `POST /api/field-mappings/delete` — remove a field mapping

### Push
- `POST /api/push/records` — upsert records into client's HubSpot
- `POST /api/push/status-update` — update property values on existing records
- `POST /api/push/link` — create associations between records

### Workflows
- `POST /api/workflows/list` — list active workflows
- `POST /api/workflows/deploy` — create/update automation rules
- `POST /api/workflows/remove` — delete deployed automations

### Internal
- `GET /health` — health check (no auth)

---

## Directory Structure

```
hubspot-engine-x/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, lifespan (db pool + httpx), mount routers, CORS, logging
│   ├── config.py             # Pydantic Settings from env vars
│   ├── db.py                 # asyncpg connection pool (init/close/get, tuned)
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── context.py        # AuthContext dataclass, ROLE_PERMISSIONS
│   │   ├── dependencies.py   # get_current_auth, validate_client_access
│   │   └── passwords.py      # bcrypt hash/verify
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── logging.py        # Structured logging, correlation IDs, audit log
│   ├── models/
│   │   ├── __init__.py
│   │   ├── admin.py
│   │   ├── auth.py
│   │   ├── clients.py
│   │   ├── connections.py
│   │   ├── crm.py
│   │   ├── field_mappings.py
│   │   ├── push.py
│   │   ├── tokens.py
│   │   └── users.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── admin.py           # Super-admin: org + user creation
│   │   ├── auth.py            # Login + /me
│   │   ├── clients.py         # Client CRUD
│   │   ├── users.py           # User management
│   │   ├── tokens.py          # API token lifecycle
│   │   ├── connections.py     # OAuth connections via Nango
│   │   ├── crm.py             # CRM read operations (search, list, get, batch-read, associations, pipelines, lists)
│   │   ├── field_mappings.py  # Field mapping CRUD
│   │   └── push.py            # Record upserts via Batch API
│   └── services/
│       ├── __init__.py
│       ├── hubspot.py         # HubSpot CRM API calls (rate limiting, retry, token caching)
│       ├── token_manager.py   # Nango client (get token, create session, delete)
│       └── push_service.py    # Batch API record upserts with field mapping + idempotency
├── supabase/
│   └── migrations/
│       └── 002_push_idempotency.sql
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_endpoints.py
│   ├── test_hubspot_service.py
│   ├── test_push_service.py
│   └── test_crm_router.py
├── .env.example
├── .gitignore
├── Dockerfile
├── railway.toml
├── requirements.txt
├── README.md
├── PROJECT_STATUS.md
└── CLAUDE.md
```

---

## Environment Variables

All secrets managed via Doppler. On Railway, set `DOPPLER_TOKEN` only.

```
DATABASE_URL=<supabase-postgres-connection-string>
JWT_SECRET=<random-secret-for-signing-jwts>
SUPER_ADMIN_JWT_SECRET=<separate-secret-for-super-admin-bearer-auth>
HUBSPOT_CLIENT_ID=<hubspot-app-client-id>
HUBSPOT_CLIENT_SECRET=<hubspot-app-client-secret>
NANGO_SECRET_KEY=<nango-api-secret-key>
NANGO_BASE_URL=https://api.nango.dev
NANGO_PROVIDER_CONFIG_KEY=hubspot
ALLOWED_ORIGINS=<comma-separated-cors-origins>
```

---

## Key Principles

1. **hubspot-engine-x never decides business logic.** It executes what the org tells it to.
2. **One HubSpot app, unlimited client connections.** Per-client OAuth managed by Nango.
3. **Tokens are managed by Nango.** Access tokens are refreshed transparently. They never touch our database, logs, or API responses.
4. **Everything is logged.** Deployments, pushes, topology pulls — all recorded with timestamps, org_id, client_id.
5. **Clean up is a first-class operation.** Deployments can be rolled back.
6. **Service layer boundary.** All HubSpot API calls go through `app/services/hubspot.py`. All Nango calls go through `app/services/token_manager.py`. No router calls external APIs directly.

---

## Common Commands

```bash
# Run locally (Doppler injects secrets)
doppler run -- .venv/bin/python -m uvicorn app.main:app --reload --port 8001

# Run tests
doppler run -- pytest tests/ -v

# Run a migration
psql "$DATABASE_URL" -f supabase/migrations/0XX_*.sql

# Deploy to Railway (auto-deploys on push to main)
git push origin main
```