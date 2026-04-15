# RBAC — APTUS API (single-tenant)

This backend runs in **single-tenant mode**. There is no organization partitioning field in the DB/API.

Roles: `SUPER_ADMIN`, `SALES_DIRECTOR`, `STATE_HEAD`, `RSM`, `DEPUTY_RSM`, `ASM`, `MR`

Legend: **Yes** = allowed · **No** = denied (403) · **Scoped** = constrained by role visibility (self / subtree / state)

## Users (`/v1/users`)

- **Read**: authenticated users can read direct reports, hierarchy, and users by id.
- **Directory**: `GET /v1/users/company` is a paginated global directory for `SUPER_ADMIN`.
- **Write**: create/update/delete users = `SUPER_ADMIN` only.

## Master data (`/v1/states`, `/v1/divisions`, `/v1/headquarters`, `/v1/locations`, `/v1/products`)

- **Read**: any authenticated user.
- **Write**: `SUPER_ADMIN` only.

## Stockists (`/v1/super-stockists`, `/v1/stockists`, `/v1/medical-stores`)

- **Super-stockists / Stockists write**: `SUPER_ADMIN` only.
- **Medical stores write**: allowed per current routes.
- **MR medical-store visibility**: still scoped by MR allocations.

## Doctors (`/v1/doctors`)

- **MR**: can list/get only allocation-visible doctors.
- **Others**: can list/get.
- **Write**: allowed per current routes.

## Allocations (`/v1/allocations`)

- **View bundle**: allowed if target MR is visible under `get_visible_mr_ids`.
- **Manage allocations**: management roles (ASM+) for visible MRs.

## Secondary sales (`/v1/secondary-sales`)

- **List/get**: scoped by visible MR ids.
- **Create**: MR (self) and `SUPER_ADMIN` (on behalf of MR).
- **Update/delete**: `SUPER_ADMIN` only.

## Reports (`/v1/reports/secondary-sales/analytics`)

- Scoped by visible MR ids; supports filters and pies.

