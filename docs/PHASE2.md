# Phase 2 — Master Data (Backend)

**Status:** Implemented (backend).

**Goal:** Expose CRUD-style APIs for geographic and product master data, with authorization in FastAPI and the repository → service → router pattern.

---

## Scope (what we build)

| Area | Entities | Notes |
|------|-----------|--------|
| Geography | `Company` (read/ensure), `State`, `Division`, `Headquarter`, `Location` | Hierarchical: Division → State → HQ → Location per spec |
| Products | `Product` | Scoped to `division_id`; prices as in schema |

**Out of scope for Phase 2:** stockists, doctors, users beyond existing endpoints, allocations, sales, reports, Next.js (`aptus-web`).

---

## API shape (aligned with spec)

Base prefix: `/v1` (existing).

Suggested routes (plural, kebab-case where multi-word):

- `GET/POST /v1/states`
- `GET/POST /v1/divisions`
- `GET/POST /v1/headquarters`
- `GET/POST /v1/locations`
- `GET/POST /v1/products`
- `PUT /v1/products/{id}` (and optionally `GET /v1/products/{id}`)

**Permissions (permission matrix):** create/update master data = **SUPER_ADMIN** only (read/list may be opened later to more roles with company/state scoping — default Phase 2: **authenticated read**, **SUPER_ADMIN write** unless you want stricter read).

**Response envelope:** existing `ok()` / `err()` + pagination meta on list endpoints.

**Pagination:** default `page=1`, `per_page=20`, max `100` on all list endpoints.

---

## Code layout

Under `app/modules/master/`:

- `router.py` — routes only; `Depends(get_current_user)`, `require_roles` for writes
- `service.py` — business rules (company scope, FK checks, soft-delete semantics)
- `repository.py` — all SQLAlchemy access (select/insert/update `is_active`)
- `schemas.py` — Pydantic v2 request/response models (`*Create`, `*Update`, `*Out`, list wrappers)

Wire **`master` router** in `app/main.py` (multiple `APIRouter` includes or one router with sub-prefixes — we’ll match your URL table above cleanly).

**Rules:** no Supabase SDK in service/repository; no raw queries in router; return `model_dump(mode="json")` through `ok()` (and `jsonable_encoder` in `ok()` already helps).

---

## Data model (already in SQLAlchemy)

Uses existing models in `app/models/master.py`:

- `Company`, `Division`, `State`, `Headquarter`, `Location`, `Product`

Validations in service layer:

- FK integrity (e.g. `headquarter.state_id`, `location.headquarter_id`, `product.division_id`)
- **Soft delete:** `is_active = false` for “delete”; no hard `DELETE`
- Company scoping: non–SUPER_ADMIN reads later; Phase 2 writes tied to `current_user.company_id` where applicable

---

## Alembic

No new tables if metadata already matches DB. If we add indexes only, optional follow-up migration — **not** required to ship Phase 2 if schema is already applied.

---

## Testing (light for Phase 2)

- `pytest` smoke: health + one authenticated list endpoint (mock or test DB) — optional first pass
- Manual verification via `/docs` same as Phase 1

---

## Deliverables checklist

- [x] `master/repository.py`, `service.py`, `schemas.py`, `router.py` implemented
- [x] Routers mounted in `main.py`
- [x] List endpoints paginated + envelope
- [x] Create/update guarded by **SUPER_ADMIN**
- [x] `.env.example` unchanged (no new vars)

---

## Verify in `/docs`

Use a **SUPER_ADMIN** token. Example flow: **POST** `/v1/states` with your `company_id`, then **GET** `/v1/states?company_id=...`. Non–super-admins see only their `company_id` (query ignored).
