# Authorization matrix (RBAC) — APTUS API

This is a **role → permissions** summary for the current backend. For deeper notes and code pointers, see `docs/RBAC.md`.

Roles (highest → lowest): `SUPER_ADMIN`, `SALES_DIRECTOR`, `STATE_HEAD`, `RSM`, `DEPUTY_RSM`, `ASM`, `MR`

Legend:
- **Yes**: allowed
- **No**: denied (403)
- **Scoped**: allowed only within the caller’s visibility/scope (company/state/subtree)

---

## 1. Users (`/v1/users`)

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `GET /users` (direct reports) | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| `GET /users/hierarchy` | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| `GET /users/{id}` | Scoped (same company) | Scoped | Scoped | Scoped | Scoped | Scoped | Yes (any company) |
| `GET /users/company` (directory) | **No** | Yes | Yes | Yes | Yes | Yes | Yes* |
| `POST /users` (create) | **No** | **No** | **No** | **No** | **No** | **No** | **Yes** |
| `PUT /users/{id}` (update) | **No** | **No** | **No** | **No** | **No** | **No** | **Yes** |

Notes:
- **SUPER_ADMIN**: `GET /users/company` requires query `company_id`.

---

## 2. Master data (`/v1/states`, `/v1/divisions`, `/v1/headquarters`, `/v1/locations`, `/v1/products`)

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `GET` list / `GET` by id | Yes (company-scoped) | Yes | Yes | Yes | Yes | Yes | Yes (optional `company_id` filter) |
| `POST` / `PUT` (create/update) | **No** | **No** | **No** | **No** | **No** | **No** | **Yes** |

---

## 3. Stockists & master parties

### 3.1 Super-stockists (`/v1/super-stockists`) and Stockists (`/v1/stockists`)

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `GET` list / `GET` by id | Yes (company-scoped) | Yes | Yes | Yes | Yes | Yes | Yes (optional `company_id` filter) |
| `POST` / `PUT` | **No** | **No** | **No** | **No** | **No** | **No** | **Yes** |

### 3.2 Medical stores (`/v1/medical-stores`)

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `GET` list / `GET` by id | Scoped† | Yes | Yes | Yes | Yes | Yes | Yes (optional `company_id` filter) |
| `POST` / `PUT` | Yes‡ | Yes‡ | Yes‡ | Yes‡ | Yes‡ | Yes‡ | Yes‡ |

Notes:
- † **MR medical-store visibility**: stores are visible only if reachable via **allocated doctors** (`mr_doctor_allocations`) + doctor links (`doctor_medical_stores`).
- ‡ **Create/update**: any authenticated user can create/update a medical store **in their own company**. **SUPER_ADMIN** can create/update any company (must send `company_id` on create).

---

## 4. Doctors (`/v1/doctors`)

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `GET` list | Scoped (allocated doctors only) | Scoped (company) | Scoped (company) | Scoped (company) | Scoped (company) | Scoped (company) | Yes (optional `company_id`) |
| `GET` by id | Scoped (only if allocated) | Scoped | Scoped | Scoped | Scoped | Scoped | Yes |
| `POST` / `PUT` | Yes (own company) | Yes (own company) | Yes (own company) | Yes (own company) | Yes (own company) | Yes (own company) | Yes* |

Notes:
- * **SUPER_ADMIN** must send `company_id` on create.
- Doctors can be linked to medical stores via `medical_store_ids` (used to derive MR→store reachability through allocations).

---

## 5. MR allocations (`/v1/allocations`)

### 5.1 Who can view allocations for an MR?

`GET /v1/allocations/mr/{mr_id}` is allowed if `mr_id` is in `UserService.get_visible_mr_ids(current_user)` and target is an active MR.

| Caller role | Typical visible MRs |
|------------|----------------------|
| MR | Self only |
| ASM | MRs in ASM subtree |
| DEPUTY_RSM / RSM / STATE_HEAD | State-scoped MRs (when `state_id` is set) |
| SALES_DIRECTOR | All active MRs in company |
| SUPER_ADMIN | All active MRs (all companies) |

### 5.2 Who can create/remove allocations?

| Allocation type | Routes | Who can manage |
|---|---|---|
| Single allocation ops | `PUT /allocations/mr/{mr_id}` | **Everyone above MR** (ASM+) |

Important:
- Allocations include **locations, doctors, medical stores, products**.

---

## 6. Secondary sales (`/v1/secondary-sales`)

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `GET /secondary-sales` (list) | Scoped (visible MRs) | Scoped | Scoped | Scoped | Scoped | Scoped | Scoped |
| `GET /secondary-sales/{id}` | Scoped | Scoped | Scoped | Scoped | Scoped | Scoped | Scoped |
| `POST /secondary-sales` (create) | **Yes** (self) | **No** | **No** | **No** | **No** | **No** | **Yes** (on behalf of MR via `mr_id`) |
| `PUT /secondary-sales/{id}` (update) | **No** | **No** | **No** | **No** | **No** | **No** | **Yes** |
| `DELETE /secondary-sales/{id}` (soft delete) | **No** | **No** | **No** | **No** | **No** | **No** | **Yes** |

---

## 7. Reports — Secondary sales analytics (`/v1/reports/secondary-sales/analytics`)

| Action | MR | ASM | DEPUTY_RSM | RSM | STATE_HEAD | SALES_DIRECTOR | SUPER_ADMIN |
|--------|----|-----|------------|-----|------------|----------------|-------------|
| `GET /reports/secondary-sales/analytics` | Scoped (self MR) | Scoped (visible MRs) | Scoped | Scoped | Scoped | Scoped | Scoped (all MRs; optional `company_id` filter) |

Notes:
- MR sees only their own data.
- Higher roles see data for MRs “below” them via `get_visible_mr_ids`.
- Supports combined filters (`mr_id`, `doctor_id`, `headquarter_id`, `location_id`, `product_id`, etc.), `timeseries_bucket`, and `pie` breakdowns.
- For `pie=rsm` or `pie=asm`: **SUPER_ADMIN must pass `company_id`** (org tree is company-local).

