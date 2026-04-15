# Authorization matrix — APTUS API (single-tenant)

Single-tenant mode: no organization partitioning field exists in the DB/API.

Legend: **Yes** = allowed · **No** = denied · **Scoped** = constrained by visibility (self / subtree / state)

## Users

- `GET /v1/users` (direct reports): **Yes** (all roles)
- `GET /v1/users/hierarchy`: **Yes** (all roles)
- `GET /v1/users/company` (directory): **Yes** (`SUPER_ADMIN`), **No** (others)
- `POST/PUT/DELETE /v1/users`: **Yes** (`SUPER_ADMIN`), **No** (others)

## Master data

- Lists and reads: **Yes** (all authenticated)
- Writes: **Yes** (`SUPER_ADMIN`), **No** (others)

## Stockists / Doctors / Allocations / Sales / Reports

See `docs/RBAC.md` for the role-by-role notes.

