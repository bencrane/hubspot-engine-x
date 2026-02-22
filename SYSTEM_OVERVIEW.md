# System Overview: hubspot-engine-x

Comprehensive documentation of the system. Written for AI agents or human engineers who need full context to continue development.

---

## What This System Is

`hubspot-engine-x` is a multi-tenant API service for programmatic HubSpot administration. It allows organizations (RevOps firms, agencies, internal teams) to manage their clients' HubSpot portals entirely through API — without ever logging into a client's HubSpot account.

One HubSpot app serves all tenants. Each client authorizes via OAuth (managed by Nango). From that point on, the owning organization can read schemas, deploy custom objects, push records, and clean up — all through hubspot-engine-x endpoints.

This is standalone infrastructure. It is not embedded in any product. Multiple products consume it:
- **Staffing Activation** — deploys job posting objects, pushes enriched leads daily
- **Revenue Activation** — manages client CRM schemas, pushes enriched pipeline data
- **Future RevOps firms** — onboard as new orgs, same API, full isolation

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| API Framework | FastAPI (Python 3.13) | Async, Pydantic models, dependency injection for AuthContext |
| Deployment | Railway (Dockerfile) | Docker build, SSL, auto-deploy on push to main |
| Database | Supabase Postgres via asyncpg | Direct connection, async, no ORM overhead |
| Secrets | Doppler | Centralized secrets management, injected at runtime |
| Auth | API tokens (SHA-256 hash) + JWT (HS256) | Machine-to-machine (tokens) and user sessions (JWT) |
| OAuth | Nango | Manages HubSpot OAuth flow, token storage, automatic refresh |
| Password Hashing | bcrypt (direct) | No passlib — direct bcrypt library |
| HTTP Client | httpx | Async HTTP for HubSpot and Nango API calls |
| External API | HubSpot CRM API v3 | All CRM operations via stored OAuth tokens in Nango |

### Why No Modal / Trigger.dev

hubspot-engine-x handles request/response workloads: OAuth flows, schema reads, record pushes, deployment operations. No long-running compute, no batch processing, no orchestration. FastAPI on Railway is sufficient.

Scheduling (e.g., daily pushes) is handled by external orchestrators (Trigger.dev, data-engine-x) that call hubspot-engine-x endpoints. This service does not schedule its own work.

---

## Multi-Tenancy Model

### Three Tiers

```
Tier 1: Organization   — The business (Revenue Activation, future RevOps firms)
Tier 2: Client          — A customer of that org whose HubSpot is being managed
Tier 3: User            — A person at that org who interacts with the API
```

### Query Scoping

Every query filters by `org_id`. Client-level queries add `client_id` after validating the client belongs to the org:

```sql
WHERE org_id = $1
WHERE org_id = $1 AND client_id = $2
```

### Denormalization

All child tables carry `org_id` for direct filtering without joins. Tenant integrity triggers enforce that `client_id` always belongs to the `org_id` on the same row.

---

## Auth Model

### Three Auth Methods

**Super-Admin** (bootstrap only):
- Bearer token matches `SUPER_ADMIN_JWT_SECRET` (constant-time comparison via `hmac.compare_digest`)
- Used only for: org creation, first user creation

**API Tokens** (machine-to-machine):
- SHA-256 hashed and stored in `api_tokens` table
- Looked up on each request → returns org_id, user_id, role
- Query enforces both `t.is_active = TRUE` and `u.is_active = TRUE`

**JWT Sessions** (user login):
- Issued on login, signed with `JWT_SECRET` (HS256)
- Contains: `org_id`, `user_id`, `role`, `client_id`, `exp`
- `exp` claim required, required claims validated, unknown roles rejected

### AuthContext

```python
@dataclass
class AuthContext:
    org_id: str
    user_id: str
    role: str
    permissions: list[str]
    client_id: str | None
    auth_method: str
```

### Permissions Matrix

| Permission | org_admin | company_admin | company_member |
|---|---|---|---|
| `connections.read` | ✓ | ✓ | ✓ |
| `connections.write` | ✓ | | |
| `topology.read` | ✓ | ✓ | ✓ |
| `deploy.write` | ✓ | | |
| `push.write` | ✓ | | |
| `workflows.read` | ✓ | ✓ | |
| `workflows.write` | ✓ | | |
| `org.manage` | ✓ | | |

---

## OAuth + Token Management (Nango)

Nango handles the full HubSpot OAuth lifecycle:

1. Our API creates a Nango connect session → returns a session token for the frontend
2. Frontend uses the token with Nango's Connect UI → user authorizes in HubSpot
3. Nango exchanges the authorization code for tokens, stores them, handles refresh automatically
4. Our `POST /api/connections/callback` endpoint confirms the connection and stores metadata (status, hub_domain, hubspot_portal_id, nango_connection_id)
5. On every HubSpot API call, `token_manager.py` calls Nango to get a fresh access token

**HubSpot refresh tokens are single-use** — each refresh issues a new refresh token that replaces the old one. Nango handles this transparently.

**Tokens never touch our database.** Nango holds all OAuth credentials. Our `crm_connections` table stores metadata only.

The `client_id` (UUID) is used as the Nango `connectionId`.

---

## Database Schema

### Tables

| Table | Purpose |
|---|---|
| `organizations` | Tenant orgs (RA, future firms) |
| `clients` | Org's customers whose HubSpot is managed |
| `users` | Org users with roles and bcrypt password hashes |
| `api_tokens` | SHA-256 hashed machine-to-machine auth tokens |
| `crm_connections` | Connection metadata — status, hub_domain, hubspot_portal_id, nango_connection_id (no tokens) |
| `crm_topology_snapshots` | Versioned JSONB schema snapshots per client |
| `crm_conflict_reports` | Pre-deploy check results (green/yellow/red) |
| `crm_deployments` | What was deployed, when, result, rollback status — includes optional `conflict_report_id` FK |
| `crm_push_logs` | Record push history with success/fail counts |
| `crm_field_mappings` | Canonical-to-HubSpot property mapping per client per object |

### Enums

| Enum | Values |
|---|---|
| `user_role` | org_admin, company_admin, company_member |
| `connection_status` | pending, connected, expired, revoked, error |
| `deployment_status` | pending, in_progress, succeeded, partial, failed, rolled_back |
| `deployment_type` | custom_object, custom_property, association, workflow, other |
| `conflict_severity` | green, yellow, red |
| `push_status` | queued, in_progress, succeeded, partial, failed |

---

## HubSpot APIs Used

| API | Purpose |
|---|---|
| Schemas API (`/crm/v3/schemas`) | Custom object CRUD |
| Properties API (`/crm/v3/properties/{objectType}`) | Property management per object |
| Associations API (`/crm/v4/associations/`) | Association types and record linking |
| Batch API (`/crm/v3/objects/{objectType}/batch/`) | Batch create/update/upsert (100 records per call) |
| Automation API (`/automation/v4/flows`) | Workflow listing |

---

## HubSpot Rate Limits

| Limit | Value |
|---|---|
| Per 10 seconds (OAuth apps) | 100 requests |
| Daily (OAuth apps) | 500,000 requests |
| Batch operations | 100 records per call |
| Search API | 5 requests per second per app |

Rate limit responses return HTTP 429 with `Retry-After` header.

---

## Environment Variables

All secrets managed via Doppler.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Supabase Postgres direct connection string |
| `JWT_SECRET` | JWT signing secret (HS256) |
| `SUPER_ADMIN_JWT_SECRET` | Separate secret for super-admin bearer auth |
| `HUBSPOT_CLIENT_ID` | HubSpot app client ID |
| `HUBSPOT_CLIENT_SECRET` | HubSpot app client secret |
| `NANGO_SECRET_KEY` | Nango API secret key |
| `NANGO_BASE_URL` | Nango API base URL (default: `https://api.nango.dev`) |
| `NANGO_PROVIDER_CONFIG_KEY` | Nango integration ID (default: `hubspot`) |

---

## Error Handling

| Code | Meaning |
|---|---|
| 401 | Missing or invalid auth token |
| 403 | Valid token, insufficient permissions |
| 404 | Not found OR belongs to different org (prevents enumeration) |
| 400 | Invalid request payload |
| 422 | Invalid request format (Pydantic validation) |
| 502 | HubSpot or Nango API error — includes original error details |

---

## Build Progress

| Phase | Status | What |
|---|---|---|
| 1 | 🔲 Next | Foundation — config, db pool, auth context/dependency, app shell |
| 2 | 🔲 | Auth + Clients + Users + API Tokens |
| 3 | 🔲 | OAuth Connections via Nango |
| 4 | 🔲 | Topology Pull + Snapshots |
| 5A | 🔲 | Conflict Detection |
| 5B | 🔲 | Deploy + Rollback |
| 6A | 🔲 | Field Mapping CRUD |
| 6B | 🔲 | Push — Batch API upserts |
| 7 | 🔲 | Workflows |

---

## Key Differences From sfdc-engine-x

| Aspect | sfdc-engine-x | hubspot-engine-x |
|---|---|---|
| CRM API surface | REST + Tooling + Metadata (SOAP) | CRM API v3 (REST/JSON only) |
| Custom object creation | Metadata API async deploy | `POST /crm/v3/schemas` (synchronous) |
| Field/property creation | Tooling API (synchronous) | `POST /crm/v3/properties/{objectType}` (synchronous) |
| Batch record limit | 200 per Composite API call | 100 per Batch API call |
| Deploy complexity | High (XML, ZIP, async polling) | Low (REST/JSON, synchronous) |
| Workflow deployment | Full Flow XML via Metadata API | Limited — primarily UI-built |
| Token refresh | Standard refresh | Single-use refresh tokens (Nango handles) |
| Connection identifier | `instance_url` + `sfdc_org_id` | `hub_domain` + `hubspot_portal_id` |