# Auth Migration Cleanup — Changelog

**Date:** 2026-03-29
**Branch:** `bencrane/auth-cleanup`
**Preceding work:** EdDSA/JWKS migration (commit `8c14b2d`)

---

## Why

The EdDSA/JWKS migration successfully moved JWT verification to auth-engine-x but left broken and dead code behind. The `POST /api/auth/login` endpoint referenced `settings.JWT_SECRET` which had been removed from config — it would crash with `AttributeError` at runtime. Passwords were still being hashed on user creation despite login being broken. Documentation still described the old HS256/JWT_SECRET model.

This cleanup removes all dead code from the incomplete migration and updates documentation to reflect the current auth architecture.

---

## What Changed

### Removed: Login endpoint

- **`app/routers/auth.py`** — Deleted the `login()` function and its route (`POST /api/auth/login`). This endpoint was broken (referenced removed `settings.JWT_SECRET`) and issued tokens with `user_id` claim instead of `sub` (mismatched with the EdDSA verifier). Removed all signing-related imports (`jwt`, `datetime`, `timedelta`, `asyncpg`, `settings`, `verify_password`). The `GET /api/auth/me` endpoint is unchanged.

- **`app/models/auth.py`** — Removed `LoginRequest`, `LoginUserDetail`, and `LoginResponse` models. `MeResponse` is unchanged.

### Removed: Password module

- **`app/auth/passwords.py`** — Deleted entirely. With login removed and passwords removed from user creation, this module had zero consumers.

### Removed: Passwords from user creation

Auth-engine-x is the sole login provider. Local passwords are no longer needed.

- **`app/routers/admin.py`** — Removed `hash_password` import and password hashing from super-admin user creation. SQL INSERT no longer includes `password_hash`.
- **`app/routers/users.py`** — Same changes for org-admin user creation.
- **`app/models/admin.py`** — Removed `password` field from `AdminCreateUserRequest`.
- **`app/models/users.py`** — Removed `password` field from `CreateUserRequest`.

### Updated: Tests

- **`tests/test_endpoints.py`** — Removed `test_login` and `test_login_wrong_password` tests. Removed `password` field from all user creation test payloads (super-admin and org-admin). All 27 remaining tests pass.

### Updated: Documentation

- **`CLAUDE.md`**, **`SYSTEM_OVERVIEW.md`**, **`ARCHITECTURE.md`** — All three updated:
  - Auth stack row: `JWT (HS256)` → `JWT (EdDSA via JWKS)`
  - Removed `Password Hashing | bcrypt` from stack table
  - JWT Sessions section rewritten: tokens issued by auth-engine-x, verified via JWKS at `https://api.authengine.dev/api/auth/jwks`. This service does not issue JWTs.
  - Removed `JWT_SECRET` from environment variables
  - Removed `POST /api/auth/login` from API endpoints listing
  - Updated `auth_method` to include `"super_admin"`
  - Removed password references from directory structure and user table descriptions

---

## Not Changed

- **`app/auth/dependencies.py`** — EdDSA/JWKS verification is correct and untouched.
- **`app/auth/context.py`** — AuthContext dataclass unchanged.
- **`app/config.py`** — Already correct (no `JWT_SECRET`).
- **Super-admin bearer auth** — `SUPER_ADMIN_JWT_SECRET` mechanism unchanged.
- **API token auth** — SHA-256 hash lookup path unchanged.
- **Database schema** — No destructive changes. `password_hash` column remains in the `users` table (nullable) but is no longer populated.
- **`.env.example`** — Already clean (`JWT_SECRET` was already removed).

---

## Open Item

**UUID alignment:** Tokens from auth-engine-x contain `sub` (user_id) and `org_id`. These must match rows in hubspot-engine-x's local `users` and `organizations` tables for tenant-scoped queries to work. If they don't match, EdDSA verification succeeds but downstream DB queries silently return empty results. This should be confirmed in production before considering the migration fully complete.
