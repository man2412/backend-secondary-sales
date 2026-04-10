# Role-based access (RBAC) — APTUS API

This document reflects **current backend behavior** in code. If you change services or routers, update this file.

**Roles** (enum `UserRole`): `SUPER_ADMIN`, `SALES_DIRECTOR`, `STATE_HEAD`, `RSM`, `DEPUTY_RSM`, `ASM`, `MR`

Legend: **Yes** = allowed · **No** = denied (403 / blocked) · **Scoped** = allowed only for data/users the role can see (company / state / subtree) · **—** = not applicable

---

## 1. Auth

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `POST /v1/auth/sync-user` (valid JWT + email) | Yes | Yes | Yes | Yes | Yes | Yes | Yes |

*Requires pre-provisioned `users` row with matching email, or existing `supabase_id` match.*

---

## 2. Users (`/v1/users`)

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `GET /users` (direct reports) | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| `GET /users/hierarchy` | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| `GET /users/{id}` | Scoped (same company) | Scoped | Scoped | Scoped | Scoped | Scoped | Yes (any company) |
| `GET /users/company` (paginated directory) | **No** | Yes | Yes | Yes | Yes | Yes | Yes* |
| `POST /users` (create) | **No** | **No** | **No** | **No** | **No** | **No** | **Yes** |
| `PUT /users/{id}` (update) | **No** | **No** | **No** | **No** | **No** | **No** | **Yes** |

\* **`SUPER_ADMIN`** must pass query `company_id`. Other roles: list is always their own `company_id` (query `company_id` ignored).

---

## 3. Master data (`/v1/states`, `divisions`, `headquarters`, `locations`, `products`)

| Action | MR | ASM | … all non–super | SUPER_ADMIN |
|--------|----|-----|-----------------|-------------|
| `GET` list | Yes (company-scoped) | Same | Same | Yes — **`company_id` query required** (no cross-company list without it) |
| `GET` by id | Yes (company-scoped) | Same | Same | Yes (any row by id; unchanged) |
| `POST` / `PUT` (create / update) | **No** | **No** | **No** | **Yes** |

---

## 4. Stockists (`/v1/super-stockists`, `/stockists`, `/medical-stores`)

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `GET` super-stockists / stockists (lists + by id) | Yes (company scope) | Yes | Yes | Yes | Yes | Yes | Yes* |
| `POST` / `PUT` super-stockists & stockists | **No** | **No** | **No** | **No** | **No** | **No** | **Yes** |
| `GET` medical-stores | Scoped† | Yes | Yes | Yes | Yes | Yes | Yes* |
| `POST` / `PUT` medical-stores | Yes‡ | Yes‡ | Yes‡ | Yes‡ | Yes‡ | Yes‡ | **Yes**‡ |

\* **`SUPER_ADMIN`:** **`company_id` query required** on lists; **`company_id` required** in body for create when super creates medical store.  
† **MR:** list/get only for **allocated** stores (`mr_store_allocations`).  
‡ **Create/update:** any authenticated user may create/update a medical store **in their own company** (`company_id` on create must match, except super). **SUPER_ADMIN** may create/update any company when `company_id` is supplied / row is in that company.

---

## 5. Doctors (`/v1/doctors`)

| Action | MR | ASM | … all company users | SUPER_ADMIN |
|--------|----|-----|---------------------|-------------|
| `GET` list | Scoped (allocated doctors only for MR) | Company-scoped list | Same | Yes — **`company_id` query required** |
| `GET` by id | If allocated | If visible in company | Same | Yes |
| `POST` / `PUT` | Yes (own company) | Yes (own company) | Yes (own company) | **Yes** (`company_id` required on create) |

\* **`SUPER_ADMIN`** needs `company_id` on list and on create.

---

## 6. MR allocations (`/v1/allocations`)

**Who may `GET` bundle** `GET /allocations/mr/{mr_id}`: anyone authenticated **if** `mr_id` is in `UserService.get_visible_mr_ids` for that user **and** target is an active **MR**.

Rough visibility:

| Caller | Typical visible MRs |
|--------|---------------------|
| **MR** | Self only |
| **ASM** | MRs in recursive report subtree under ASM |
| **DEPUTY_RSM / RSM / STATE_HEAD** | MRs in same company with same `state_id` on user (when `state_id` set) |
| **SALES_DIRECTOR** | All active MRs in company |
| **SUPER_ADMIN** | All active MRs (all companies) |

**Allocations include medical stores directly** (`mr_store_allocations`).

**Who may manage allocations (single endpoint)**

| Role | Add/remove allocations |
|------|-----------------------------|
| **MR** | **No** |
| **ASM** | **Yes** (for visible MRs) |
| **DEPUTY_RSM** | **Yes** (for visible MRs) |
| **RSM** | **Yes** (for visible MRs) |
| **STATE_HEAD** | **Yes** (for visible MRs) |
| **SALES_DIRECTOR** | **Yes** (for visible MRs) |
| **SUPER_ADMIN** | **Yes** (for visible MRs) |

Same **company / FK** validation as before (location, doctor+division, store, product must belong to MR’s company).

---

## 7. Secondary sales (`/v1/secondary-sales`)

| Action | MR | ASM | … management | SUPER_ADMIN |
|--------|----|-----|----------------|-------------|
| `GET` list / `GET` by id | Scoped (visible MR ids + company) | Scoped | Scoped | Scoped† |
| `POST` (create) | **Yes** (allocations + rules) | **No** | **No** | **Yes** (must send `mr_id` for the MR on whose behalf the sale is recorded) |
| `PUT` / `DELETE` (soft delete) | **No** | **No** | **No** | **Yes** (any sale) |

† **SUPER_ADMIN** must pass query **`company_id`** on list and get (same as other list APIs); rows are filtered to that company and visible MRs.

---

## 8. Reports (`/v1/reports/secondary-sales/analytics`)

Single **`GET /secondary-sales/analytics`** replaces separate summary / by-MR / by-product / by-division routes.

| Action | MR | Management / SUPER_ADMIN |
|--------|----|---------------------------|
| Analytics (summary, time series, pie charts) | **Own MR only** (`get_visible_mr_ids`) | Scoped to visible MRs; **SUPER_ADMIN** must pass **`company_id`** (400 if missing) |

**Query highlights:** `date_from`, `date_to`; **`company_id`** required for **SUPER_ADMIN**; optional `mr_id`, `doctor_id`, `headquarter_id`, `location_id`, `product_id`, `division_id`, `state_id`, `include_inactive`. **`timeseries_bucket`:** `day` \| `week` \| `month`. **`pie`:** comma-separated `product`, `location`, `headquarter` (or `hq`), `division`, `rsm`, `asm`. Non–super users are scoped to their company automatically.

---

## 9. Quick “who can allocate an MR?”

- **Cannot:** **MR**  
- **Can:** **ASM, DEPUTY_RSM, RSM, STATE_HEAD, SALES_DIRECTOR, SUPER_ADMIN** — but only for an **`mr_id`** they are allowed to **view** (see §6).

---

## 10. Code pointers

| Area | File(s) |
|------|---------|
| Allocation managers | `app/modules/allocations/service.py` — `_ALLOCATION_MANAGER_ROLES`, `_require_can_manage_allocations` |
| Who can see which MRs | `app/modules/users/service.py` — `get_visible_mr_ids` |
| Master writes | `app/modules/master/service.py` — `_ensure_super_admin` |
| Users admin | `app/modules/users/service.py` — `create_user`, `update_user`, `list_company_users` |
| Stockists | `app/modules/stockists/service.py` |
| Doctors | `app/modules/doctors/service.py` |
| Sales | `app/modules/sales/service.py` |
| Reports | `app/modules/reports/service.py`, `repository.py`, `router.py` |

Last updated: unified secondary-sales **analytics** endpoint; medical-store and doctor write access for all company users; **SUPER_ADMIN** + **MR** may create secondary sales.
