"""Pydantic response schemas for the entity-import API."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class EntityImportCounts(BaseModel):
    """Per-entity-type tally for an ingestion run."""

    inserted: int = 0
    matched_existing: int = 0
    merged_duplicates: int = 0


class StoreMergeRecord(BaseModel):
    """Record of an in-sheet duplicate group that mapped to a single store."""

    canonical_name: str
    variants: list[str]
    medical_store_id: uuid.UUID | None = None
    matched_existing: bool = False


class EntityImportWarning(BaseModel):
    row_index: int | None = None
    kind: str  # 'missing_headquarter', 'ambiguous_mr', 'unknown_mr', 'unknown_doctor', ...
    message: str


class EntityImportFailure(BaseModel):
    row_index: int
    message: str


class EntityImportSummary(BaseModel):
    """Final response shape for `POST /entity-import/upload`."""

    filename: str
    total_rows: int
    processed_rows: int
    skipped_rows: int

    stockists: EntityImportCounts
    headquarters: EntityImportCounts
    medical_stores: EntityImportCounts
    doctors: EntityImportCounts
    doctor_store_links: EntityImportCounts
    mr_doctor_allocations: EntityImportCounts

    merged_store_groups: list[StoreMergeRecord] = Field(default_factory=list)
    warnings: list[EntityImportWarning] = Field(default_factory=list)
    failures: list[EntityImportFailure] = Field(default_factory=list)

    elapsed_ms: float = 0.0
