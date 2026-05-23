"""
Post-LLM deterministic validator.

Checks each structured row from the LLM for:
- Required fields present and correct types
- UUIDs actually exist in the DB (FK sanity check)
- Date parseable
- Numeric fields parseable

Adds an "errors" list and "is_valid" boolean to each row dict.
Does NOT modify or reject rows — callers decide what to skip.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor import Doctor
from app.models.master import Product
from app.models.stockist import MedicalStore
from app.models.user import User


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_int(value: Any) -> int | None:
    f = _parse_float(value)
    return int(f) if f is not None else None


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


async def _exists(db: AsyncSession, model: type, pk: uuid.UUID) -> bool:
    r = await db.execute(select(model).where(model.id == pk))  # type: ignore[attr-defined]
    return r.scalar_one_or_none() is not None


async def validate_rows(
    db: AsyncSession,
    rows: list[dict],
) -> list[dict]:
    """
    Validate each row in-place, returning the annotated list.
    Adds: errors (list[str]), is_valid (bool), and coerced typed fields.

    `mr_id` is expected to already be set on each row by
    `ImportService._resolve_mrs_from_stores` (medical store → doctor → MR
    allocation). Rows still lacking an mr_id at this point fail validation
    and the user must assign one in preview.
    """
    # Collect unique IDs to batch-check existence
    product_ids: set[uuid.UUID] = set()
    store_ids: set[uuid.UUID] = set()
    doctor_ids: set[uuid.UUID] = set()
    mr_ids: set[uuid.UUID] = set()

    for row in rows:
        if pid := _parse_uuid(row.get("product_id")):
            product_ids.add(pid)
        if sid := _parse_uuid(row.get("medical_store_id")):
            store_ids.add(sid)
        if did := _parse_uuid(row.get("doctor_id")):
            doctor_ids.add(did)
        if mid := _parse_uuid(row.get("mr_id")):
            mr_ids.add(mid)

    # Batch existence checks
    async def existing_ids(model: type, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        if not ids:
            return set()
        r = await db.execute(select(model.id).where(model.id.in_(ids)))  # type: ignore[attr-defined]
        return {row[0] for row in r.all()}

    valid_products = await existing_ids(Product, product_ids)
    valid_stores = await existing_ids(MedicalStore, store_ids)
    valid_doctors = await existing_ids(Doctor, doctor_ids)
    valid_mrs = await existing_ids(User, mr_ids)

    for row in rows:
        errors: list[str] = []

        # --- product_id (required) ---
        pid = _parse_uuid(row.get("product_id"))
        if pid is None:
            errors.append("product_id: missing or unresolved — assign a product before committing")
        elif pid not in valid_products:
            errors.append(f"product_id: {pid} not found in products table")
        row["product_id"] = str(pid) if pid else None

        # --- sale_date (required) ---
        sale_date = _parse_date(row.get("sale_date"))
        if sale_date is None:
            errors.append(f"sale_date: cannot parse {row.get('sale_date')!r}")
        row["sale_date"] = sale_date.isoformat() if sale_date else None

        # --- sale_qty (required) ---
        qty = _parse_int(row.get("sale_qty"))
        if qty is None or qty < 0:
            errors.append(f"sale_qty: invalid value {row.get('sale_qty')!r}")
        row["sale_qty"] = qty

        # --- free_qty (optional, default 0) ---
        row["free_qty"] = _parse_int(row.get("free_qty")) or 0

        # --- numeric optionals ---
        row["mrp"] = _parse_float(row.get("mrp"))
        row["ptr"] = _parse_float(row.get("ptr"))
        row["reported_amount"] = _parse_float(row.get("reported_amount"))

        # --- medical_store_id (required for import / commit) ---
        sid = _parse_uuid(row.get("medical_store_id"))
        if sid is None:
            errors.append(
                "medical_store_id: missing or unresolved — assign a medical store before committing"
            )
        elif sid not in valid_stores:
            errors.append(f"medical_store_id: {sid} not found in medical_stores table")
            sid = None
        row["medical_store_id"] = str(sid) if sid else None

        # --- doctor_id (optional) ---
        did = _parse_uuid(row.get("doctor_id"))
        if did is not None and did not in valid_doctors:
            errors.append(f"doctor_id: {did} not found in doctors table")
            did = None
        row["doctor_id"] = str(did) if did else None

        # --- mr_id (required — must be auto-resolved from store→doctor→MR
        #             allocation, or assigned manually in preview) ---
        row_mr = _parse_uuid(row.get("mr_id"))
        if row_mr is None:
            errors.append(
                "mr_id: could not auto-resolve from medical store's doctor "
                "allocations — assign manually in preview"
            )
        elif row_mr not in valid_mrs:
            errors.append(f"mr_id: {row_mr} not found in users table")
            row_mr = None
        row["mr_id"] = str(row_mr) if row_mr else None

        row["errors"] = errors
        row["is_valid"] = len(errors) == 0

    return rows
