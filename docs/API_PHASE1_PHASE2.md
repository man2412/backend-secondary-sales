# Frontend API contract — APTUS API

Use this document together with **`{BASE_URL}/openapi.json`** and **`{BASE_URL}/docs`**.  
Role rules: **`docs/RBAC.md`**.

**`{BASE_URL}`** — e.g. `http://localhost:8000`. Authenticated JSON API prefix: **`/v1`**.

---

## Table of contents

1. [How every request/response works](#1-how-every-requestresponse-works)
2. [Reference: JSON shapes (`data` objects)](#2-reference-json-shapes-data-objects)
3. [Health & auth](#3-health--auth)
4. [Users](#4-users)
5. [Master data (states → products)](#5-master-data-states--products)
6. [Stockists](#6-stockists)
7. [Doctors](#7-doctors)
8. [MR allocations](#8-mr-allocations)
9. [Secondary sales](#9-secondary-sales)
10. [Reports — secondary sales analytics](#10-reports--secondary-sales-analytics)
11. [Route index](#11-route-index)

---

## 1. How every request/response works

### 1.1 Headers

| Header | Value | When |
|--------|--------|------|
| `Authorization` | `Bearer <access_token>` | All `/v1/...` routes (Supabase session access token) |
| `Content-Type` | `application/json` | `POST` / `PUT` with a body |

### 1.2 Success envelope (most `/v1` routes)

```json
{
  "success": true,
  "message": "Operation successful",
  "data": null,
  "pagination": null
}
```

- **`pagination`** is omitted when there is no pagination (not `null` in JSON — the key is absent).
- Paginated list responses include **`pagination`**:

```json
{
  "success": true,
  "message": "Operation successful",
  "data": [],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 42,
    "total_pages": 3
  }
}
```

### 1.3 Errors (4xx / 5xx)

Not wrapped in `success` / `data`. Typical shapes:

```json
{ "detail": "Human readable message" }
```

or validation:

```json
{
  "detail": [
    { "type": "...", "loc": ["body", "field"], "msg": "...", "input": ... }
  ]
}
```

### 1.4 Types in JSON

| Concept | JSON |
|---------|------|
| UUID | string, e.g. `"550e8400-e29b-41d4-a716-446655440000"` |
| date | `"YYYY-MM-DD"` |
| datetime | ISO 8601 string |
| `UserRole` | `"SUPER_ADMIN"` \| `"SALES_DIRECTOR"` \| `"STATE_HEAD"` \| `"RSM"` \| `"DEPUTY_RSM"` \| `"ASM"` \| `"MR"` |
| boolean | `true` / `false` |

### 1.5 Standard list query parameters (master + stockists + doctors)

Used on **`GET`** list endpoints unless the endpoint says otherwise:

| Query | Type | Required | Default | Rules |
|-------|------|----------|---------|--------|
| `page` | integer | no | `1` | ≥ 1 |
| `per_page` | integer | no | `20` | 1–100 |
| `company_id` | UUID | no | — | **SUPER_ADMIN:** optional filter. **Others:** ignored; server uses caller’s company |
| `include_inactive` | boolean | no | `false` | `true` includes inactive rows |

---

## 2. Reference: JSON shapes (`data` objects)

Use these as the canonical field lists when reading **`data`** or array elements inside **`data`**.

### 2.1 `User` (`UserOut`)

```json
{
  "id": "uuid",
  "supabase_id": "uuid",
  "company_id": "uuid",
  "division_id": null,
  "employee_code": null,
  "full_name": "string",
  "email": "user@example.com",
  "phone": null,
  "role": "MR",
  "reports_to": null,
  "state_id": null,
  "is_active": true,
  "created_at": "2025-01-01T12:00:00+00:00",
  "updated_at": "2025-01-01T12:00:00+00:00"
}
```

### 2.2 `POST /v1/users` body (`UserCreate`)

```json
{
  "company_id": "uuid",
  "division_id": null,
  "employee_code": null,
  "full_name": "string",
  "email": "user@example.com",
  "phone": null,
  "role": "MR",
  "reports_to": null,
  "state_id": null,
  "supabase_id": null
}
```

Required: `company_id`, `full_name`, `email`, `role`. Omit `supabase_id` to let the server assign.

### 2.3 `PUT /v1/users/{user_id}` body (`UserUpdate`)

All fields optional (send only what changes):

```json
{
  "division_id": null,
  "employee_code": null,
  "full_name": "string",
  "email": "user@example.com",
  "phone": null,
  "role": "RSM",
  "reports_to": null,
  "state_id": null,
  "is_active": true
}
```

### 2.4 `HierarchyNode` (`GET /v1/users/hierarchy` items)

```json
{
  "id": "uuid-string",
  "full_name": "string",
  "email": "string",
  "role": "MR",
  "reports_to": "uuid-string or null",
  "depth": 0
}
```

### 2.5 Master: `StateOut` / `DivisionOut` / `HeadquarterOut` / `LocationOut` / `ProductOut`

**State**

```json
{
  "id": "uuid",
  "company_id": "uuid",
  "name": "string",
  "code": null,
  "is_active": true,
  "created_at": "...",
  "updated_at": "..."
}
```

**Division** — same idea; fields: `id`, `company_id`, `name`, `is_active`, `created_at`, `updated_at`.

**Headquarter** — `id`, `state_id`, `division_id`, `name`, `is_active`, `created_at`, `updated_at`.

**Location** — `id`, `headquarter_id`, `name`, `is_active`, `created_at`, `updated_at`.

**Product**

```json
{
  "id": "uuid",
  "division_id": "uuid",
  "name": "string",
  "pack_size": null,
  "mrp": 100.0,
  "ptr": 71.42,
  "pts": 64.28,
  "hsn_code": null,
  "is_active": true,
  "created_at": "...",
  "updated_at": "..."
}
```

### 2.6 Stockists

**SuperStockistOut / StockistOut** — `id`, `company_id`, optional codes/address/`location_id`, `is_active`, timestamps. **StockistOut** also has `super_stockist_id` (nullable).

**MedicalStoreOut** — `id`, `company_id`, `stockist_id` (nullable), `name`, optional codes, `location_id`, `is_active`, timestamps.

### 2.7 `DoctorOut`

```json
{
  "id": "uuid",
  "company_id": "uuid",
  "full_name": "string",
  "specialization": null,
  "qualification": null,
  "phone": null,
  "address": null,
  "location_id": null,
  "is_active": true,
  "medical_store_ids": ["uuid"],
  "created_at": "...",
  "updated_at": "..."
}
```

### 2.8 `AllocationsBundleOut` (`GET /v1/allocations/mr/{mr_id}` → `data`)

```json
{
  "locations": [
    {
      "id": "uuid",
      "mr_id": "uuid",
      "location_id": "uuid",
      "location_name": "string or null",
      "allocated_by": "uuid",
      "allocated_at": "...",
      "is_active": true
    }
  ],
  "doctors": [
    {
      "id": "uuid",
      "mr_id": "uuid",
      "doctor_id": "uuid",
      "doctor_name": "string or null",
      "division_id": "uuid",
      "division_name": "string or null",
      "allocated_by": "uuid",
      "allocated_at": "...",
      "is_active": true
    }
  ],
  "medical_stores": [
    {
      "medical_store_id": "uuid",
      "store_name": "string or null",
      "id": "uuid",
      "mr_id": "uuid",
      "allocated_by": "uuid",
      "allocated_at": "...",
      "is_active": true
    }
  ],
  "products": [
    {
      "id": "uuid",
      "mr_id": "uuid",
      "product_id": "uuid",
      "product_name": "string or null",
      "allocated_by": "uuid",
      "allocated_at": "...",
      "is_active": true
    }
  ]
}
```

### 2.9 `SecondarySaleOut`

```json
{
  "id": "uuid",
  "mr_id": "uuid",
  "product_id": "uuid",
  "doctor_id": null,
  "medical_store_id": null,
  "division_id": "uuid",
  "headquarter_id": "uuid",
  "location_id": "uuid",
  "state_id": "uuid",
  "company_id": "uuid",
  "sale_date": "2026-03-24",
  "sale_qty": 5,
  "free_qty": 0,
  "ptr": 71.42,
  "pts": 64.28,
  "mrp": 100.0,
  "special_price": null,
  "total_amount": 357.1,
  "remarks": null,
  "is_active": true,
  "created_at": "...",
  "updated_at": "..."
}
```

`total_amount` is server-computed from `sale_qty` and `COALESCE(special_price, ptr)`. Sending `special_price: 0` in create/update is stored as `null` (use PTR).

---

## 3. Health & auth

---

### `GET /health`

| | |
|--|--|
| **Full URL** | `{BASE_URL}/health` |
| **Auth** | None |
| **Path parameters** | None |
| **Query parameters** | None |
| **Request body** | None |

**Response `200`** (plain JSON, **not** the `success` envelope):

```json
{ "status": "ok" }
```

---

### `POST /v1/auth/sync-user`

| | |
|--|--|
| **Full URL** | `{BASE_URL}/v1/auth/sync-user` |
| **Auth** | `Authorization: Bearer <access_token>` |
| **Path parameters** | None |
| **Query parameters** | None |
| **Request body** | None (empty body) |

**Response `200`**

```json
{
  "success": true,
  "message": "User synced",
  "data": {
    "user": { },
    "linked": true
  }
}
```

- **`data.user`** — same shape as [§2.1 `User`](#21-user-userout).
- **`data.linked`** — `true` if this call linked JWT to a provisioned user.

| Errors | |
|--------|--|
| `400` | Business / validation error from service |
| `404` | No provisioned user for JWT email |

---

## 4. Users

---

### `GET /v1/users`

| | |
|--|--|
| **Full URL** | `{BASE_URL}/v1/users` |
| **Auth** | Bearer |
| **Path parameters** | None |
| **Query parameters** | None |
| **Request body** | None |

**Response `200`**

```json
{
  "success": true,
  "message": "Operation successful",
  "data": [  ]
}
```

- **`data`** — array of [§2.1 `User`](#21-user-userout); active users with `reports_to === current user id`.

---

### `GET /v1/users/company`

| | |
|--|--|
| **Full URL** | `{BASE_URL}/v1/users/company` |
| **Auth** | Bearer |
| **Path parameters** | None |
| **Query parameters** | See table |
| **Request body** | None |

| Query | Type | Required | Default |
|-------|------|----------|---------|
| `page` | integer | no | `1` (≥1) |
| `per_page` | integer | no | `20` (1–100) |
| `company_id` | UUID | **yes for SUPER_ADMIN** | — |
| `include_inactive` | boolean | no | `false` |

**Response `200`**

```json
{
  "success": true,
  "message": "Operation successful",
  "data": [  ],
  "pagination": { "page": 1, "per_page": 20, "total": 0, "total_pages": 0 }
}
```

- **`data[]`** — [§2.1 `User`](#21-user-userout)

| Errors | |
|--------|--|
| `403` | Role **MR** (not allowed) |
| `400` | e.g. SUPER_ADMIN without `company_id` |

---

### `POST /v1/users`

| | |
|--|--|
| **Full URL** | `{BASE_URL}/v1/users` |
| **Auth** | Bearer + role **SUPER_ADMIN** |
| **Path parameters** | None |
| **Query parameters** | None |
| **Request body** | `application/json` — [§2.2 `UserCreate`](#22-post-v1users-body-usercreate) |

**Response `200`**

```json
{
  "success": true,
  "message": "User created",
  "data": {  }
}
```

- **`data`** — [§2.1 `User`](#21-user-userout)

| Errors | |
|--------|--|
| `403` | Not SUPER_ADMIN |

---

### `GET /v1/users/hierarchy`

| | |
|--|--|
| **Full URL** | `{BASE_URL}/v1/users/hierarchy` |
| **Auth** | Bearer |
| **Path parameters** | None |
| **Query parameters** | None |
| **Request body** | None |

**Response `200`**

```json
{
  "success": true,
  "message": "Operation successful",
  "data": [  ]
}
```

- **`data[]`** — [§2.4 `HierarchyNode`](#24-hierarchynode-get-v1usershierarchy-items)

---

### `GET /v1/users/{user_id}`

| | |
|--|--|
| **Full URL** | `{BASE_URL}/v1/users/{user_id}` |
| **Auth** | Bearer |
| **Path parameters** | `user_id` (UUID) |
| **Query parameters** | None |
| **Request body** | None |

**Response `200`**

```json
{
  "success": true,
  "message": "Operation successful",
  "data": {  }
}
```

- **`data`** — [§2.1 `User`](#21-user-userout)

| Errors | |
|--------|--|
| `404` | User not found |
| `403` | Non–super and different `company_id` |

---

### `PUT /v1/users/{user_id}`

| | |
|--|--|
| **Full URL** | `{BASE_URL}/v1/users/{user_id}` |
| **Auth** | Bearer + **SUPER_ADMIN** |
| **Path parameters** | `user_id` (UUID) |
| **Query parameters** | None |
| **Request body** | `application/json` — [§2.3 `UserUpdate`](#23-put-v1usersuser_id-body-userupdate) |

**Response `200`**

```json
{
  "success": true,
  "message": "User updated",
  "data": {  }
}
```

---

## 5. Master data (states → products)

All under `{BASE_URL}/v1`. **Reads:** any authenticated user (company scoping per [§1.5](#15-standard-list-query-parameters-master--stockists--doctors)). **POST/PUT:** **SUPER_ADMIN** only.

Below, **list `GET`** uses [§1.5](#15-standard-list-query-parameters-master--stockists--doctors) query params unless noted. **List response** shape:

```json
{
  "success": true,
  "message": "Operation successful",
  "data": [  ],
  "pagination": { "page": 1, "per_page": 20, "total": 0, "total_pages": 0 }
}
```

`data[]` element type is named per resource.

---

### `GET /v1/states` — `data[]` = `StateOut` ([§2.5](#25-master-stateout--divisionout--headquarterout--locationout--productout))

### `GET /v1/states/{state_id}` — `data` = single `StateOut`

### `POST /v1/states`

| Path / query | None beyond standard |
| **Body** | `application/json` |

```json
{
  "company_id": "uuid",
  "name": "Maharashtra",
  "code": "MH"
}
```

Required: `company_id`, `name`. **Response `200`:** `data` = `StateOut`, `message` e.g. created.

### `PUT /v1/states/{state_id}`

**Body** (all optional): `name`, `code`, `is_active`. **Response `200`:** `data` = `StateOut`.

---

### `GET /v1/divisions` — `data[]` = `DivisionOut`

### `GET /v1/divisions/{division_id}` — `data` = `DivisionOut`

### `POST /v1/divisions`

```json
{
  "company_id": "uuid",
  "name": "Division name",
  "code": "DIV-001"
}
```

**Response `200`:** `data` = `DivisionOut`.

### `PUT /v1/divisions/{division_id}`

**Body:** optional `name`, `code`, `is_active`. **Response `200`:** `data` = `DivisionOut`.

---

### `GET /v1/headquarters` — `data[]` = `HeadquarterOut`

### `GET /v1/headquarters/{hq_id}` — `data` = `HeadquarterOut`

### `POST /v1/headquarters`

```json
{
  "state_id": "uuid",
  "division_id": "uuid",
  "name": "HQ name",
  "code": "HQ-001"
}
```

**Response `200`:** `data` = `HeadquarterOut`.

### `PUT /v1/headquarters/{hq_id}`

**Body:** optional `name`, `code`, `is_active`. **Response `200`:** `data` = `HeadquarterOut`.

---

### `GET /v1/locations` — `data[]` = `LocationOut`

### `GET /v1/locations/{location_id}` — `data` = `LocationOut`

### `POST /v1/locations`

```json
{
  "headquarter_id": "uuid",
  "name": "Location name",
  "code": "LOC-001"
}
```

**Response `200`:** `data` = `LocationOut`.

### `PUT /v1/locations/{location_id}`

**Body:** optional `name`, `code`, `is_active`. **Response `200`:** `data` = `LocationOut`.

---

### `GET /v1/products` — `data[]` = `ProductOut`

### `GET /v1/products/{product_id}` — `data` = `ProductOut`

### `POST /v1/products`

```json
{
  "division_id": "uuid",
  "name": "Product name",
  "pack_size": null,
  "mrp": 100.0,
  "ptr": 71.42,
  "pts": 64.28,
  "hsn_code": null
}
```

Required: `division_id`, `name`, `mrp`, `ptr`, `pts` (each ≥ 0). **Response `200`:** `data` = `ProductOut`.

### `PUT /v1/products/{product_id}`

**Body (optional):** `name`, `pack_size`, `mrp`, `ptr`, `pts`, `hsn_code`, `is_active` (numerics ≥ 0 if sent). **Response `200`:** `data` = `ProductOut`.

---

## 6. Stockists

Paths: **`/v1/super-stockists`**, **`/v1/stockists`**, **`/v1/medical-stores`**.

**List `GET`** — same query params as [§1.5](#15-standard-list-query-parameters-master--stockists--doctors). **List response** = paginated array of the resource out-model.

| Resource | GET list `data[]` type | POST body (JSON) | PUT body |
|----------|------------------------|------------------|----------|
| Super stockist | `SuperStockistOut` | `company_id`*, `name`*, optional codes, `location_id` | optional `name`, codes, `location_id`, `is_active` |
| Stockist | `StockistOut` | `company_id`*, `name`*, optional `super_stockist_id`, codes, `location_id` | optional `super_stockist_id`, name, codes, `location_id`, `is_active` |
| Medical store | `MedicalStoreOut` | `name`*, **`company_id` required if SUPER_ADMIN**; optional `stockist_id`, codes, `location_id` | optional `stockist_id`, name, codes, `location_id`, `is_active` |

\* Required fields.

**Auth**

- **GET** — Bearer; company-scoped. **MR** medical-store list = stores reachable via allocated doctors only.
- **POST/PUT super-stockists & stockists** — **SUPER_ADMIN** only.
- **POST/PUT medical-stores** — any user in the same company as the record (super may set `company_id` on create).

**`GET` by id** — `data` = single out-model; `404` if not found / out of scope.

**`POST` success** — `data` = created row, `message` e.g. `"Created"`.  
**`PUT` success** — `data` = updated row, `message` e.g. `"Updated"`.

---

## 7. Doctors

Base: **`/v1/doctors`**.

---

### `GET /v1/doctors`

| Query | Same as [§1.5](#15-standard-list-query-parameters-master--stockists--doctors) |
| **Response `200`** | Paginated; **`data[]`** = [§2.7 `DoctorOut`](#27-doctorout) |

---

### `GET /v1/doctors/{doctor_id}`

| Path | `doctor_id` (UUID) |
| **Response `200`** | `data` = [§2.7 `DoctorOut`](#27-doctorout) |
| `404` | Not found / MR not allocated to doctor |

---

### `POST /v1/doctors`

| **Body** | `application/json` |

```json
{
  "company_id": null,
  "full_name": "Dr. Name",
  "specialization": null,
  "qualification": null,
  "phone": null,
  "address": null,
  "location_id": null,
  "medical_store_ids": []
}
```

- **`full_name`** required. **`SUPER_ADMIN`:** **`company_id` required**. Others: omit `company_id` or match own company.

**Response `200`**

```json
{
  "success": true,
  "message": "Created",
  "data": {  }
}
```

`data` = [§2.7 `DoctorOut`](#27-doctorout).

---

### `PUT /v1/doctors/{doctor_id}`

**Body** (all optional): `full_name`, `specialization`, `qualification`, `phone`, `address`, `location_id`, `is_active`, `medical_store_ids`.

**Response `200`:** `data` = [§2.7 `DoctorOut`](#27-doctorout), `message` `"Updated"`.

---

## 8. MR allocations

Base: **`/v1/allocations`**.  
Medical stores for an MR are allocated directly under the MR.

**Who may POST/DELETE allocations:** management roles only (**not** MR). See **`docs/RBAC.md`**.

---

### `GET /v1/allocations/mr/{mr_id}`

| | |
|--|--|
| **Path parameters** | `mr_id` (UUID) — target MR |
| **Query parameters** | `include_inactive` (boolean, default `false`) |
| **Request body** | None |

**Response `200`**

```json
{
  "success": true,
  "message": "Operation successful",
  "data": {  }
}
```

- **`data`** — [§2.8 `AllocationsBundleOut`](#28-allocationsbundleout-get-v1allocationsmrmr_id--data)

| Errors | |
|--------|--|
| `403` | Caller cannot view this MR’s allocations |
| `400` | Target not an active MR |

---

### `PUT /v1/allocations/mr/{mr_id}` (single allocations API)

Use this endpoint to **add/remove** locations, doctors, medical stores, and products in one request.

**Body** (`AllocationOps`)

```json
{
  "add_locations": ["uuid"],
  "remove_location_alloc_ids": ["uuid"],
  "add_doctors": [{ "doctor_id": "uuid", "division_id": "uuid" }],
  "remove_doctor_alloc_ids": ["uuid"],
  "add_stores": ["uuid"],
  "remove_store_alloc_ids": ["uuid"],
  "add_products": ["uuid"],
  "remove_product_alloc_ids": ["uuid"]
}
```

**Response `200`**: returns the updated `AllocationsBundleOut` as `data`.

---

## 9. Secondary sales

Base: **`/v1/secondary-sales`**.

---

### `GET /v1/secondary-sales`

| | |
|--|--|
| **Auth** | Bearer |
| **Path parameters** | None |
| **Query parameters** | See table |
| **Request body** | None |

| Query | Type | Required | Default |
|-------|------|----------|---------|
| `page` | integer | no | `1` |
| `per_page` | integer | no | `20` (1–100) |
| `sale_date` | date (`YYYY-MM-DD`) | no | — filter |
| `mr_id` | UUID | no | — must be visible to caller |
| `include_inactive` | boolean | no | `false` |

**Response `200`**

```json
{
  "success": true,
  "message": "Operation successful",
  "data": [  ],
  "pagination": { "page": 1, "per_page": 20, "total": 0, "total_pages": 0 }
}
```

- **`data[]`** — [§2.9 `SecondarySaleOut`](#29-secondarysaleout)

---

### `GET /v1/secondary-sales/{sale_id}`

| Path | `sale_id` (UUID) |
| **Response `200`** | `data` = [§2.9 `SecondarySaleOut`](#29-secondarysaleout) |
| `404` | Not found or MR not visible to caller |

---

### `POST /v1/secondary-sales`

| | |
|--|--|
| **Auth** | Bearer — **MR** or **SUPER_ADMIN** |
| **Body** | `application/json` |

```json
{
  "mr_id": null,
  "product_id": "uuid",
  "doctor_id": null,
  "medical_store_id": null,
  "location_id": "uuid",
  "sale_date": "2026-03-24",
  "sale_qty": 5,
  "free_qty": 0,
  "special_price": null,
  "remarks": null
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `mr_id` | **SUPER_ADMIN only** | Omit for MR (self). Super must set to selling MR’s user id |
| `product_id`, `location_id`, `sale_date`, `sale_qty` | yes | `sale_qty` ≥ 1 |
| `doctor_id` / `medical_store_id` | no | Business rules: at least one may be required; store must be linked to an allocated doctor |
| `free_qty` | no | default 0, ≥ 0 |
| `special_price` | no | `null` = bill at PTR; avoid sending `0` to mean PTR (server normalizes 0 → null) |

**Response `200`**

```json
{
  "success": true,
  "message": "Sale created",
  "data": {  }
}
```

`data` = [§2.9 `SecondarySaleOut`](#29-secondarysaleout).

---

### `PUT /v1/secondary-sales/{sale_id}`

| | |
|--|--|
| **Auth** | Bearer + **SUPER_ADMIN** only |
| **Path** | `sale_id` (UUID) |
| **Body** | `application/json` — all optional |

```json
{
  "sale_qty": 5,
  "free_qty": 0,
  "special_price": null,
  "remarks": null
}
```

**Response `200`:** `data` = [§2.9 `SecondarySaleOut`](#29-secondarysaleout), `message` e.g. `"Sale updated"`.

| Errors | |
|--------|--|
| `403` | Not SUPER_ADMIN |

---

### `DELETE /v1/secondary-sales/{sale_id}`

| | |
|--|--|
| **Auth** | Bearer + **SUPER_ADMIN** only |
| **Path** | `sale_id` (UUID) |
| **Body** | None |

**Response `200`**

```json
{
  "success": true,
  "message": "Sale removed"
}
```

Soft-deletes the sale (`is_active: false`).

---

### `POST /v1/secondary-sales/import`

Uploads an **Excel (`.xlsx`) or PDF (`.pdf`)** and inserts rows into secondary sales.

| | |
|--|--|
| **Auth** | Bearer + **SUPER_ADMIN** |
| **Body** | `multipart/form-data` with field `file` |

**Expected table columns (headers)**: `mr_id`, `product_id`, `location_id`, `sale_date`, `sale_qty`, optional `free_qty`, `doctor_id`, `medical_store_id`, `special_price`, `remarks`.

**Response `200`**

```json
{
  "success": true,
  "message": "Import complete",
  "data": {
    "created": 10,
    "failed": 2,
    "errors": [
      { "row": 3, "error": "Sale qty must be at least 1", "data": {} }
    ]
  }
}
```

---

## 10. Reports — secondary sales analytics

---

### `GET /v1/reports/secondary-sales/analytics`

| | |
|--|--|
| **Auth** | Bearer |
| **Path parameters** | None |
| **Query parameters** | See tables |
| **Request body** | None |

**Required**

| Query | Type | Example |
|-------|------|--------|
| `date_from` | date (or ISO datetime; **date part used**) | `2026-03-01` |
| `date_to` | date | `2026-03-31` |

**Optional filters**

| Query | Type | Notes |
|-------|------|--------|
| `company_id` | UUID | SUPER_ADMIN only |
| `mr_id` | UUID | Must be visible to caller |
| `doctor_id`, `headquarter_id`, `location_id`, `product_id`, `division_id`, `state_id` | UUID | AND filters on sales |
| `include_inactive` | boolean | default `false` |
| `include_summary` | boolean | default `true` |

**Optional outputs**

| Query | Values | Effect |
|-------|--------|--------|
| `timeseries_bucket` | `day` \| `week` \| `month` | Adds `data.time_series[]` |
| `pie` | comma-separated | Adds `data.pies[]`. Tokens: `product`, `location`, `headquarter` or `hq`, `division`, `rsm`, `asm` |

For **`pie`** including **`rsm`** or **`asm`**, **SUPER_ADMIN** must pass **`company_id`**.

**Response `200`**

```json
{
  "success": true,
  "message": "Operation successful",
  "data": {
    "date_from": "2026-03-01",
    "date_to": "2026-03-31",
    "filters": {
      "company_id": null,
      "mr_id": null,
      "doctor_id": null,
      "headquarter_id": null,
      "location_id": null,
      "product_id": null,
      "division_id": null,
      "state_id": null,
      "include_inactive": false
    },
    "summary": {
      "line_count": 10,
      "total_sale_qty": 100,
      "total_free_qty": 5,
      "total_amount": 5000.0
    },
    "time_series": null,
    "pies": []
  }
}
```

- If `include_summary=false`, **`summary`** is `null`.
- If `timeseries_bucket` is set, **`time_series`** is an array of `{ "period": "string", "revenue": 0.0, "sale_qty": 0, "free_qty": 0 }`.
- **`pies`** — each element: `{ "dimension": "product", "slices": [ { "id": "uuid", "label": "string", "revenue": 0.0, "sale_qty": 0, "pct_revenue": 25.5, "pct_quantity": 30.0 } ] }`.

| Errors | |
|--------|--|
| `400` | Invalid date range, unknown `pie` token, missing `company_id` for RSM/ASM pie as super |
| `403` | e.g. `mr_id` not allowed |

---

## 11. Route index

| Method | Path |
|--------|------|
| GET | `/health` |
| POST | `/v1/auth/sync-user` |
| GET | `/v1/users` |
| GET | `/v1/users/company` |
| POST | `/v1/users` |
| GET | `/v1/users/hierarchy` |
| GET | `/v1/users/{user_id}` |
| PUT | `/v1/users/{user_id}` |
| GET | `/v1/states` |
| GET | `/v1/states/{state_id}` |
| POST | `/v1/states` |
| PUT | `/v1/states/{state_id}` |
| GET | `/v1/divisions` |
| GET | `/v1/divisions/{division_id}` |
| POST | `/v1/divisions` |
| PUT | `/v1/divisions/{division_id}` |
| GET | `/v1/headquarters` |
| GET | `/v1/headquarters/{hq_id}` |
| POST | `/v1/headquarters` |
| PUT | `/v1/headquarters/{hq_id}` |
| GET | `/v1/locations` |
| GET | `/v1/locations/{location_id}` |
| POST | `/v1/locations` |
| PUT | `/v1/locations/{location_id}` |
| GET | `/v1/products` |
| GET | `/v1/products/{product_id}` |
| POST | `/v1/products` |
| PUT | `/v1/products/{product_id}` |
| GET | `/v1/super-stockists` |
| GET | `/v1/super-stockists/{entity_id}` |
| POST | `/v1/super-stockists` |
| PUT | `/v1/super-stockists/{entity_id}` |
| GET | `/v1/stockists` |
| GET | `/v1/stockists/{entity_id}` |
| POST | `/v1/stockists` |
| PUT | `/v1/stockists/{entity_id}` |
| GET | `/v1/medical-stores` |
| GET | `/v1/medical-stores/{entity_id}` |
| POST | `/v1/medical-stores` |
| PUT | `/v1/medical-stores/{entity_id}` |
| GET | `/v1/doctors` |
| GET | `/v1/doctors/{doctor_id}` |
| POST | `/v1/doctors` |
| PUT | `/v1/doctors/{doctor_id}` |
| DELETE | `/v1/doctors/{doctor_id}` |
| GET | `/v1/allocations/mr/{mr_id}` |
| PUT | `/v1/allocations/mr/{mr_id}` |
| GET | `/v1/secondary-sales` |
| GET | `/v1/secondary-sales/{sale_id}` |
| POST | `/v1/secondary-sales` |
| PUT | `/v1/secondary-sales/{sale_id}` |
| DELETE | `/v1/secondary-sales/{sale_id}` |
| POST | `/v1/secondary-sales/import` |
| GET | `/v1/reports/secondary-sales/analytics` |

---

## CORS

Browser clients: frontend origin must appear in the API **`ALLOWED_ORIGINS`** env (comma-separated).
