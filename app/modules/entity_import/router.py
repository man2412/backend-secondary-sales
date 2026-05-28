"""
POST /entity-import/upload

Accepts an .xlsx / .csv pharmaceutical CRM pool sheet and idempotently
ingests stockists → headquarters → medical stores → doctors → MR-doctor
allocations into the existing schema. Returns a structured summary.

Optional form fields `state_id` and `division_ids` let the caller supply
defaults so any HQ the sheet references but the master data is missing
gets auto-created on the fly (Headquarter requires both).
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_roles
from app.core.database import get_db
from app.core.responses import ok
from app.models.enums import UserRole
from app.models.user import User
from app.modules.entity_import.parser import parse_sheet
from app.modules.entity_import.repository import EntityImportRepository
from app.modules.entity_import.schemas import EntityImportSummary
from app.modules.entity_import.service import EntityImportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/entity-import", tags=["entity-import"])

# Only the same roles that already manage allocations can upload pool sheets.
_IMPORT_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.SALES_DIRECTOR,
    UserRole.STATE_HEAD,
    UserRole.RSM,
    UserRole.DEPUTY_RSM,
    UserRole.ASM,
)


def _parse_division_ids(raw: str | None) -> list[uuid.UUID]:
    """
    Accept `division_ids` as either a single comma-separated value or a
    JSON array string. Whitespace around items is ignored; empty input
    returns []. Raises ValueError on a malformed UUID so the caller can
    convert it to a 400.
    """
    if not raw:
        return []
    txt = raw.strip()
    if not txt:
        return []
    # Allow either '["uuid", "uuid"]' or 'uuid,uuid,uuid'.
    if txt.startswith("[") and txt.endswith("]"):
        txt = txt[1:-1]
    parts = [p.strip().strip('"').strip("'") for p in txt.split(",")]
    out: list[uuid.UUID] = []
    for p in parts:
        if not p:
            continue
        out.append(uuid.UUID(p))
    return out


async def _validate_hq_defaults(
    db: AsyncSession,
    *,
    state_id: uuid.UUID | None,
    division_ids: list[uuid.UUID],
) -> None:
    """Ensure that any provided state/division ids actually exist in master data."""
    if state_id is None and not division_ids:
        return
    if (state_id is None) ^ (not division_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "state_id and division_ids must be provided together to "
                "auto-create missing headquarters"
            ),
        )
    repo = EntityImportRepository()
    if state_id is not None:
        state = await repo.get_state(db, state_id)
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"state_id={state_id} not found",
            )
    if division_ids:
        found = await repo.list_divisions_by_ids(db, division_ids)
        found_ids = {d.id for d in found}
        missing = [str(d) for d in division_ids if d not in found_ids]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"division_ids not found: {missing}",
            )


@router.post("/upload", response_model=None)
async def upload_entity_import(
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[User, Depends(require_roles(*_IMPORT_ROLES))],
    file: UploadFile = File(..., description="XLSX or CSV pool sheet"),
    state_id: Annotated[
        uuid.UUID | None,
        Form(
            description=(
                "Optional state UUID applied when auto-creating any HQ that "
                "the sheet references but master data is missing."
            ),
        ),
    ] = None,
    division_ids: Annotated[
        str | None,
        Form(
            description=(
                "Optional comma-separated (or JSON-array) list of division "
                "UUIDs applied when auto-creating any HQ that the sheet "
                "references but master data is missing."
            ),
        ),
    ] = None,
) -> dict:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing filename — cannot infer file format",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    try:
        division_id_list = _parse_division_ids(division_ids)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid division_ids value: {exc}",
        ) from exc

    await _validate_hq_defaults(db, state_id=state_id, division_ids=division_id_list)

    try:
        rows = parse_sheet(file.filename, content)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:
        logger.exception("entity-import: failed to parse %r", file.filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse sheet: {exc}",
        ) from exc

    logger.info(
        "entity-import: parsed file=%r rows=%d uploaded_by=%s "
        "default_state_id=%s default_divisions=%d",
        file.filename, len(rows), str(current.id)[:8],
        state_id, len(division_id_list),
    )

    try:
        summary: EntityImportSummary = await EntityImportService().ingest(
            db, rows,
            filename=file.filename,
            uploaded_by=current.id,
            default_state_id=state_id,
            default_division_ids=division_id_list or None,
        )
    except Exception as exc:
        logger.exception("entity-import: ingestion failed for %r", file.filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {exc}",
        ) from exc

    return ok(data=summary.model_dump(mode="json"), message="Ingestion complete")
