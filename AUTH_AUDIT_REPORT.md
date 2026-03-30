# Auth Implementation Audit — hubspot-engine-x

## Critical Finding (Read This First)

**The JWT verification was migrated to EdDSA/JWKS in commit `8c14b2d`, but the login endpoint is now broken.** `app/routers/auth.py:62` references `settings.JWT_SECRET`, which was removed from `app/config.py` during the migration. The login endpoint will crash with `AttributeError` at runtime. This means **hubspot-engine-x can verify centralized JWTs but can no longer issue its own.**

This is either an incomplete migration (login should have been removed or updated) or an unintentional regression.

---

## 1. JWT Library

- **Library:** `PyJWT[crypto]>=2.8.0` (the `[crypto]` extra enables EdDSA/RSA support via `cryptography`)
- **Dependency file:** `requirements.txt:16`
- Import: `import jwt` and `from jwt import InvalidTokenError, PyJWKClient`

---

## 2. JWT Signing (Token Creation)

- **File:** `app/routers/auth.py:55-62`
- **Function:** `login()` endpoint handler

```python
payload = {
    "org_id": str(row["org_id"]),
    "user_id": str(row["id"]),
    "role": str(row["role"]),
    "client_id": str(row["client_id"]) if row["client_id"] is not None else None,
    "exp": datetime.now(timezone.utc) + timedelta(hours=24),
}
token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
```

- **Claims:** `org_id`, `user_id`, `role`, `client_id` (nullable), `exp`
- **TTL:** 24 hours
- **Algorithm:** HS256 (local symmetric secret)
- **BROKEN:** `settings.JWT_SECRET` no longer exists in `app/config.py` — this will crash at runtime.
- **Note:** The issued token uses `user_id` as the claim key, but the verification code (section 3) expects `sub`. This was always a mismatch — the old HS256 verifier presumably also used `user_id`, but the new EdDSA verifier requires `sub`.

---

## 3. JWT Verification

- **File:** `app/auth/dependencies.py:94-128`
- **Function:** `get_current_auth()` — the JWT path (after API token lookup fails)

```python
_jwks_client = PyJWKClient(
    "https://api.authengine.dev/api/auth/jwks",
    cache_jwk_set=True,
    lifespan=300,
)

# Inside get_current_auth():
try:
    signing_key = _jwks_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["EdDSA"],
        issuer="https://api.authengine.dev",
        audience="https://api.authengine.dev",
        options={"require": ["exp", "sub", "org_id", "role"]},
    )
except InvalidTokenError:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication token",
    ) from None

org_id = claims.get("org_id")
user_id = claims.get("sub")      # ← maps "sub" to user_id
role = claims.get("role")
client_id = claims.get("client_id")
```

- **Algorithm:** EdDSA only
- **JWKS endpoint:** `https://api.authengine.dev/api/auth/jwks` (5-minute cache)
- **Required claims:** `exp`, `sub`, `org_id`, `role`
- **Validated:** `issuer` = `https://api.authengine.dev`, `audience` = `https://api.authengine.dev`
- **Claim remapping:** `sub` → `user_id` (line 111)
- **Post-decode validation:** `org_id`, `user_id`, `role` must all be truthy; `role` must exist in `ROLE_PERMISSIONS`

---

## 4. User Management / Login

- **Login endpoint:** `POST /api/auth/login` — `app/routers/auth.py:17-74`
- **User storage:** `users` table with fields: `id`, `org_id`, `email`, `name`, `role`, `client_id`, `password_hash`, `is_active`, `created_at`
- **Password hashing:** bcrypt (direct) — `app/auth/passwords.py`

```python
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
```

- **Other auth endpoints:**
  - `GET /api/auth/me` — returns current auth context
  - `POST /api/super-admin/users` — creates users (bootstrap only, requires super-admin bearer token)
  - No register, password reset, or invite endpoints exist.

**Login handler** (`app/routers/auth.py:18-74`):
```python
@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: asyncpg.Connection = Depends(get_db)):
    rows = await db.fetch("""
        SELECT u.id, u.org_id, u.email, u.name, u.role, u.client_id, u.password_hash
        FROM users u
        INNER JOIN organizations o ON o.id = u.org_id
        WHERE u.email = $1 AND u.is_active = TRUE AND o.is_active = TRUE
    """, request.email)

    if len(rows) != 1:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    row = rows[0]
    if row["password_hash"] is None or not verify_password(request.password, str(row["password_hash"])):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    payload = {
        "org_id": str(row["org_id"]),
        "user_id": str(row["id"]),
        "role": str(row["role"]),
        "client_id": str(row["client_id"]) if row["client_id"] is not None else None,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")  # ← BROKEN
    return LoginResponse(token=token, user=LoginUserDetail(...))
```

---

## 5. Auth Middleware

There is **no auth middleware** that runs on every request. Auth is enforced per-endpoint via FastAPI dependency injection:

- **Standard endpoints:** `auth: AuthContext = Depends(get_current_auth)` — `app/auth/dependencies.py:51`
- **Permission-gated endpoints:** `Depends(require_permission("deploy.write"))` — `app/auth/dependencies.py:158`
- **Super-admin endpoints:** `dependencies=[Depends(require_super_admin)]` — `app/auth/dependencies.py:131`

**Public/unprotected paths:**
- `GET /health` — no auth
- `POST /api/auth/login` — no auth (it IS the auth endpoint)

**Logging middleware** exists at `app/middleware/logging.py` — mounted in `app/main.py:79`. It logs requests and audit events but does NOT perform any auth checks.

**Auth failure handling:** All three auth paths raise `HTTPException(401)` with a generic `"Invalid or missing authentication token"` message.

---

## 6. API Key / Fallback Auth

The auth resolution chain in `get_current_auth()` (`app/auth/dependencies.py:51-128`):

1. Extract bearer token from `Authorization` header
2. SHA-256 hash the token
3. Look up the hash in `api_tokens` table (joined with `users`)
4. If found → return `AuthContext` with `auth_method="api_token"`
5. If not found → attempt EdDSA JWT decode via JWKS
6. If JWT valid → return `AuthContext` with `auth_method="session"`
7. If JWT invalid → raise 401

**API token lookup query** (`app/auth/dependencies.py:57-73`):
```python
SELECT t.id AS token_id, t.org_id, t.user_id, u.role, u.client_id
FROM api_tokens t
INNER JOIN users u ON u.id = t.user_id
WHERE t.token_hash = $1
  AND t.is_active = TRUE
  AND u.is_active = TRUE
  AND (t.expires_at IS NULL OR t.expires_at > NOW())
```

After a successful API token lookup, `last_used_at` is updated asynchronously (best-effort).

---

## 7. Config

- **`SUPER_ADMIN_JWT_SECRET`:** Defined in `app/config.py:8` — used only in `app/auth/dependencies.py:133` for super-admin bearer auth.
- **`JWT_SECRET`:** **No longer in `app/config.py`.** Still referenced in `app/routers/auth.py:62` (broken).
- **Other auth-related env vars:** None beyond `SUPER_ADMIN_JWT_SECRET`.
- **`JWT_SECRET` references outside signing/verification:**
  - `CLAUDE.md`, `SYSTEM_OVERVIEW.md`, `ARCHITECTURE.md` — documentation still references it
  - `.env.example` — does NOT include `JWT_SECRET` (already cleaned up)
  - Test files reference only `SUPER_ADMIN_JWT_SECRET`

---

## 8. CORS Configuration

- **File:** `app/main.py:68-76`

```python
_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
```

- Origins come from the `ALLOWED_ORIGINS` env var (comma-separated).
- If empty, CORS middleware is not added at all.
- Credentials, all methods, and all headers are allowed.

---

## 9. User/Org Model

- **hubspot-engine-x has its own `users` table with passwords.** This is a local user store, not shared with any other service.
- **It has its own `organizations` table** — full multi-tenant model with `org_id` scoping on every table.
- **ID alignment is unknown.** The `org_id` and `user_id` values in locally-issued JWTs (from the now-broken login endpoint) are UUIDs from the local database. Whether the centralized auth service at `api.authengine.dev` uses the same IDs is an open question. The JWT verifier maps `sub` → `user_id` and uses `org_id` directly from the token — so the centralized service must issue tokens with matching IDs for the database queries to work.

**This is the key migration question:** Are the users and orgs in hubspot-engine-x's database the same entities (same UUIDs) as in the centralized auth service? If not, the EdDSA JWT verification will produce `org_id`/`user_id` values that don't match any rows in the local database, and all downstream queries will return empty results.

---

## 10. Who Uses This Service

Based on the codebase:

- **Primary consumers are other services and internal tools** — the API token system (machine-to-machine auth) is the primary auth path. `data-engine-x`, trigger.dev tasks, and other integrations use API tokens.
- **There is a user-facing login flow** (`POST /api/auth/login`) suggesting a frontend exists or was planned, but the login endpoint is currently broken.
- **The users table has email/password** — these are org-level users (RevOps operators), not end consumers. They are likely the same people who manage HubSpot portals.
- **Whether these users are the same as PaidEdge/GTMDirect users is unclear from the code alone.** The user model is self-contained. The recent JWKS migration suggests an intent to unify with the centralized auth model, but the local user store hasn't been removed.

---

## Summary of Migration State

| Component | Status |
|-----------|--------|
| JWT verification | **Migrated** — EdDSA via JWKS against `api.authengine.dev` |
| JWT issuance (login) | **Broken** — references removed `JWT_SECRET`, would crash at runtime |
| API token auth | **Unchanged** — still works, local to hubspot-engine-x |
| Super-admin auth | **Unchanged** — still uses `SUPER_ADMIN_JWT_SECRET` bearer token |
| Local user store | **Still exists** — `users` table with passwords, not removed |
| `JWT_SECRET` config | **Removed from Settings** — but still referenced in login code |
| Documentation | **Stale** — CLAUDE.md, ARCHITECTURE.md, SYSTEM_OVERVIEW.md still describe HS256 |

### What Needs Decision

1. **Should the login endpoint be removed?** If users authenticate via the centralized auth service, hubspot-engine-x doesn't need to issue its own JWTs. The login endpoint becomes dead code.
2. **Should the local users table be kept?** It may still be needed for API token → user lookups, but password hashing and login can be removed.
3. **Are the UUIDs aligned?** The centralized JWTs must contain `org_id` and `sub` (user_id) values that exist in hubspot-engine-x's database for tenant scoping to work.
4. **Should the password module be removed?** If login is removed, `app/auth/passwords.py` is only needed by the super-admin user creation endpoint (which still hashes passwords on insert).
