"""
Sheet → list[ImportRow] parser.

Supports the two formats the project already accepts (XLSX, CSV). Header
matching is case-insensitive and tolerant of leading/trailing whitespace.
Duplicate headers (the sheet has two columns named "LOCATION") are handled
by ordinal: the first match wins for `doctor_location`, the second for
`chemist_location`. That mirrors the layout of the reference sheet:
columns 8 (doctor location) and 12 (chemist location).
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import openpyxl

from app.modules.entity_import.normalize import clean_whitespace


# Canonical field name → list of accepted header spellings (lowercased).
# Order matters: alternative spellings are tried in order. When a sheet has
# two columns with the same header we resolve via `_HEADER_DUPS` below.
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "doctor_name": ("dr name", "doctor name", "doctor_name", "physician name"),
    "doctor_location": ("location", "doctor location", "dr location"),
    "doctor_address": ("address", "doctor address", "clinic address"),
    "specialty": ("speciality", "speciality ", "speciliaty", "speciality/department"),
    "degree": ("degree", "qualification"),
    "contact": ("contact number", "contact", "phone"),
    "registration": ("registration number", "registration"),
    "birthdate": ("birthdate", "dob", "birth date"),
    "anniversary": ("anniversary",),
    "clinic_open_date": ("clinic open date", "clinic opening date"),
    "activity": ("activity",),
    "chemist_name": ("chemist name", "medical store", "store name", "chemist"),
    "chemist_location": ("location", "chemist location", "store location"),
    "fso_name": ("fso name", "mr name", "fos name", "field officer", "fso", "mr"),
    "headquarter": ("head quarter", "headquarter", "hq", "head quarters"),
    "rsm": ("rsm",),
    "asm": ("asm",),
    "stockist_name": ("stokist name", "stockist name", "stockist", "stokist", "distributor"),
}

# Canonical fields that share the spelling "location" with another field
# and therefore need ordinal disambiguation.
_HEADER_DUPS: tuple[tuple[str, str, int], ...] = (
    # (canonical_target, header_spelling, occurrence_index 0-based)
    ("doctor_location", "location", 0),
    ("chemist_location", "location", 1),
)


@dataclass
class ImportRow:
    """One row parsed from the input sheet, all values cleaned of whitespace."""

    row_index: int  # 1-based, excluding the header row, for log/error reporting
    doctor_name: str
    doctor_location: str
    doctor_address: str
    chemist_name: str
    chemist_location: str
    fso_name: str
    headquarter: str
    rsm: str
    asm: str
    stockist_name: str
    specialty: str = ""
    degree: str = ""
    contact: str = ""
    registration: str = ""
    birthdate: str = ""
    anniversary: str = ""
    clinic_open_date: str = ""
    activity: str = ""

    def is_blank(self) -> bool:
        """A row is blank when nothing identifying is present (doctor + chemist both empty)."""
        return not (self.doctor_name or self.chemist_name)


def _build_index_map(header_row: list[str]) -> dict[str, int]:
    """
    Build canonical_field → column_index mapping from a header row.

    Steps:
      1. Lower-case + trim each header cell.
      2. For each canonical field, scan its alias list and pick the first
         column whose normalized text matches.
      3. Apply `_HEADER_DUPS` overrides so duplicated headers like the two
         "LOCATION" columns route to the right canonical field.
    """
    normalized = [str(h or "").strip().lower() for h in header_row]
    idx: dict[str, int] = {}

    for canonical, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                idx[canonical] = normalized.index(alias)
                break

    # Disambiguate columns whose header text repeats (e.g. two "LOCATION"s).
    for canonical, spelling, occurrence in _HEADER_DUPS:
        positions = [i for i, h in enumerate(normalized) if h == spelling]
        if len(positions) > occurrence:
            idx[canonical] = positions[occurrence]

    return idx


def _cell_value(row: tuple, col_idx: int | None) -> str:
    if col_idx is None or col_idx >= len(row):
        return ""
    return clean_whitespace(row[col_idx])


def _row_from_tuple(row_tuple: tuple, idx_map: dict[str, int], row_index: int) -> ImportRow:
    g = lambda key: _cell_value(row_tuple, idx_map.get(key))  # noqa: E731
    return ImportRow(
        row_index=row_index,
        doctor_name=g("doctor_name"),
        doctor_location=g("doctor_location"),
        doctor_address=g("doctor_address"),
        chemist_name=g("chemist_name"),
        chemist_location=g("chemist_location"),
        fso_name=g("fso_name"),
        headquarter=g("headquarter"),
        rsm=g("rsm"),
        asm=g("asm"),
        stockist_name=g("stockist_name"),
        specialty=g("specialty"),
        degree=g("degree"),
        contact=g("contact"),
        registration=g("registration"),
        birthdate=g("birthdate"),
        anniversary=g("anniversary"),
        clinic_open_date=g("clinic_open_date"),
        activity=g("activity"),
    )


def parse_xlsx(content: bytes) -> list[ImportRow]:
    """Parse the first worksheet of an XLSX file into ImportRow records."""
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header = list(next(rows))
    except StopIteration:
        return []
    idx_map = _build_index_map(header)
    out: list[ImportRow] = []
    for n, r in enumerate(rows, start=1):
        if r is None:
            continue
        row = _row_from_tuple(tuple(r), idx_map, n)
        if row.is_blank():
            continue
        out.append(row)
    return out


def parse_csv(content: bytes) -> list[ImportRow]:
    """Parse a UTF-8 (or cp1252) CSV file into ImportRow records."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    idx_map = _build_index_map(header)
    out: list[ImportRow] = []
    for n, r in enumerate(reader, start=1):
        if not r:
            continue
        row = _row_from_tuple(tuple(r), idx_map, n)
        if row.is_blank():
            continue
        out.append(row)
    return out


def parse_sheet(filename: str, content: bytes) -> list[ImportRow]:
    """Dispatch on file extension. Raises ValueError for unsupported types."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("xlsx", "xls", "xlsm"):
        return parse_xlsx(content)
    if ext == "csv":
        return parse_csv(content)
    raise ValueError(
        f"Unsupported file format: {ext!r}. Supported: xlsx, xls, xlsm, csv"
    )
