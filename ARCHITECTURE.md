# Architecture — hubspot-engine-x

## What This Is

A multi-tenant API service for programmatic HubSpot administration. Organizations use it to manage their clients' HubSpot portals entirely through API — without ever logging into a client's HubSpot account.

---

## Stack Decisions

| Layer | Choice | Why |
|-------|--------|-----|
| API Framework | FastAPI (Python 3.13) | Async, Pydantic models, dependency injection for AuthContext |
| Deployment | Railway (Dockerfile) | Docker build, SSL, auto-deploy on push to main |
| Database | Supabase Postgres via asyncpg | Direct async connection pool, no ORM overhead |
| Secrets | Doppler | Centralized secrets management, injected at runtime |
| Auth | API tokens (SHA-256 hash) + JWT (HS256) | Machine-to-machine (tokens) and user sessions (JWT) |
| OAuth | Nango | Manages HubSpot OAuth flow, token storage, automatic refresh |
| Password Hashing | bcrypt (direct) | No passlib — direct bcrypt library |
| HTTP Client | httpx | Async HTTP for HubSpot and Nango API calls |
| External API | HubSpot CRM API v3 | All CRM operations via stored OAuth tokens in Nango |

### Why No Modal

This service handles synchronous request/response operations: OAuth exchanges, HubSpot API calls, database reads/writes. No long-running compute, no batch processing, no serverless scaling needed. FastAPI on Railway is the right fit.

### Why Separate From sfdc-engine-x

Salesforce and HubSpot have fundamentally different APIs, object models, rate limits, and deployment mechanisms. Merging them into one service creates a codebase full of conditionals. Two clean services, each an expert in one CRM, is simpler to build, test, and debug. The caller routes to the right engine based on the client's CRM type.

---

## Why This Exists Separately

This engine exists as standalone infrastructure — not embedded in any product — to enforce clean boundaries:

- **Multiple products share it** — Staffing Activation, Revenue Activation, future products
- **Each product is a tenant** — not a fork, not a separate deployment
- **CRM-specific logic is contained** — no other service needs to know how HubSpot works
- **HubSpot API changes don't ripple** — only this service adapts

---

## Multi-Tenancy Model

### Three Tiers

```
Tier 1: Organization    — The business (RA, future RevOps firms)
Tier 2: Client           — Customer of that org (staffing agency, SaaS company, etc.)
Tier 3: User             — Person at the org
```

### Data Isolation

```
┌─────────────────────────────────────────────────────────┐
│                    Single Database                       │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │   RA        │  │   Org B     │  │   Org C     │     │
│  │  org_id=1   │  │  org_id=2   │  │  org_id=3   │     │
│  │             │  │             │  │             │     │
│  │ - clients   │  │ - clients   │  │ - clients   │     │
│  │ - connections│ │ - connections│ │ - connections│     │
│  │ - deploys   │  │ - deploys   │  │ - deploys   │     │
│  │ - pushes    │  │ - pushes    │  │ - pushes    │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
└─────────────────────────────────────────────────────────┘
```

### Query Scoping

Every database query filters by `org_id` — no exceptions. Client-level resources additionally filter by `client_id` after validating the client belongs to the org.

```sql
-- Org-level: "show me all clients in my org"
SELECT * FROM clients WHERE org_id = $1

-- Client-level: "show me connection for this client"
SELECT * FROM crm_connections WHERE org_id = $1 AND client_id = $2
```

### Denormalization

Child tables include `org_id` even when they reference a parent that already has it. This avoids joins for tenant filtering and provides defense in depth.

| Table | Parent | Has own org_id? | Why? |
|-------|--------|-----------------|------|
| clients | organizations | Yes | Primary tenant key |
| users | organizations | Yes | Direct tenant filtering, integrity trigger |
| crm_connections | clients | Yes | Query efficiency, direct filtering |
| crm_topology_snapshots | crm_connections | Yes | Can query snapshots directly by org |
| crm_deployments | crm_connections | Yes | Can query deployments directly by org |
| crm_conflict_reports | crm_connections | Yes | Can query reports directly by org |
| crm_push_logs | crm_connections | Yes | Can query push logs directly by org |
| crm_field_mappings | clients | Yes | Can query mappings directly by org |

Tenant integrity triggers on all child tables enforce that `client_id` belongs to `org_id` on insert/update — preventing cross-tenant data leaks at the database level.

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
- Query enforces both `t.is_active = TRUE` and `u.is_active = TRUE` (token and user must both be active)
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

| Role | Scope | Can do |
|------|-------|--------|
| `org_admin` | Org-wide | Everything — connect, read, deploy, push, manage users/clients |
| `company_admin` | Client-scoped | View connections, topology, workflows |
| `company_member` | Client-scoped | Read-only |

### Permissions

| Permission | org_admin | company_admin | company_member |
|-----------|-----------|---------------|----------------|
| `connections.read` | ✓ | ✓ | ✓ |
| `connections.write` | ✓ | | |
| `topology.read` | ✓ | ✓ | ✓ |
| `deploy.write` | ✓ | | |
| `push.write` | ✓ | | |
| `workflows.read` | ✓ | ✓ | |
| `workflows.write` | ✓ | | |
| `org.manage` | ✓ | | |

---

## HubSpot Integration

### OAuth Flow (Nango-Managed)

Nango handles the full HubSpot OAuth lifecycle. No tokens are stored in our database.

```
1. Org admin initiates connection for a client
2. Our API creates a Nango connect session → returns a session token
3. Frontend uses the session token with Nango's Connect UI
4. Client's HubSpot admin authorizes in HubSpot
5. Nango exchanges the auth code for tokens, stores them
6. Frontend calls our callback endpoint to confirm
7. We store connection metadata (status, hub_domain, hubspot_portal_id) — never tokens
```

The `client_id` (UUID) is used as the Nango `connectionId`, creating a 1:1 mapping between our clients and Nango connections.

### Token Lifecycle

Managed entirely by Nango. Our `token_manager.get_valid_token()` calls Nango's `GET /connections/{id}` endpoint, which auto-refreshes the access token if expired and returns a valid one.

- Callers never deal with HubSpot auth — they get a ready-to-use access token
- HubSpot refresh tokens are single-use — each refresh issues a new refresh token. Nango handles this transparently.
- If Nango's refresh fails (HTTP 424), the connection is marked `expired` in our database
- Tokens never appear in our database, logs, or API responses

### HubSpot APIs Used

| API | Purpose |
|-----|---------|
| CRM API v3 (`/crm/v3/`) | Objects, properties, associations, records, batch operations |
| Schemas API (`/crm/v3/schemas`) | Custom object definitions — create, read, update, delete |
| Properties API (`/crm/v3/properties/{objectType}`) | Property (field) management per object type |
| Associations API (`/crm/v4/associations/`) | Association types and record linking |
| Batch API (`/crm/v3/objects/{objectType}/batch/`) | Batch create, update, upsert records (100 per call) |
| Automation API (`/automation/v4/flows`) | Workflow listing and management |

### Service Layer Boundary

All HubSpot API calls go through `app/services/hubspot.py`. All Nango calls go through `app/services/token_manager.py`. No router calls external APIs directly.

---

## Topology Pull

Topology pull captures a client's full CRM schema as a versioned snapshot.

### How It Works

1. **List object schemas** — `GET /crm/v3/schemas` returns all custom object definitions
2. **List properties per object** — `GET /crm/v3/properties/{objectType}` for each standard and custom object
3. **List association types** — `GET /crm/v4/associations/{fromObjectType}/{toObjectType}/labels`
4. **List pipelines** — `GET /crm/v1/pipelines/{objectType}` for deal/ticket pipelines
5. **Store as snapshot** — Full topology stored as a single JSONB document in `crm_topology_snapshots`, versioned per client

Each snapshot is immutable. Subsequent pulls create new versions.

### Conflict Detection — What We Check

| Check | Severity | Description |
|-------|----------|-------------|
| Object name collision | Red | Custom object with same name already exists |
| Property name collision | Yellow | Property with same name on target object exists |
| Required properties on standard objects | Red | Required properties you won't be populating |
| Active workflows | Yellow | Workflows that fire on create/update of target objects |
| Pipeline requirements | Yellow | Object requires a pipeline stage you haven't specified |

---

## Error Handling

| Code | Meaning |
|------|---------|
| 400 | Invalid request payload |
| 401 | Missing or invalid auth token |
| 403 | Valid token but insufficient permissions |
| 404 | Resource not found or belongs to different org (same response to prevent enumeration) |
| 422 | Invalid request format — Pydantic validation (e.g., malformed UUID) |
| 502 | HubSpot or Nango API error — response includes original error code and message |

HubSpot-specific errors (rate limits, invalid property, etc.) are wrapped in 502 with structured error details so the caller can handle them appropriately.

---

## Key Principles

1. **hubspot-engine-x never decides business logic.** It executes what the org tells it to.
2. **One HubSpot app, unlimited client connections.** Per-client OAuth managed by Nango.
3. **Tokens are managed by Nango.** Access tokens are refreshed transparently. They never touch our database, logs, or API responses.
4. **Everything is logged.** Every deployment, push, topology pull — recorded with timestamps, org_id, client_id.
5. **Clean up is first-class.** Deployments can be rolled back. Objects, properties — all removable.
6. **Service layer boundary.** All HubSpot API calls go through `app/services/hubspot.py`. All Nango calls go through `app/services/token_manager.py`. No router calls external APIs directly.