"""
Extraction layer for Approach G (Gemini Files API + Backend Entity Resolution).

PDFs   → raw bytes returned as-is; Gemini Files API handles native parsing.
        FOS/MR name detected cheaply from first two pages only.

Tabular (CSV / XLSX / XLS) → converted to minimal CSV text for direct LLM input.
        FOS name detected from meta-rows above the header.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


_FOS_KEYWORDS = re.compile(
    r"\b(fos|fos\s*name|mr|mr\s*name|rep|representative|field\s*officer|medical\s*rep)\b",
    re.IGNORECASE,
)


@dataclass
class ExtractionResult:
    raw_text: str | None          # tabular files — minimal CSV text
    raw_bytes: bytes | None       # PDF — sent as-is to Gemini Files API
    is_pdf: bool
    detected_fos_name: str | None = None
    total_pages: int | None = None
    total_rows: int | None = None


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def extract_pdf(
    content: bytes,
    *,
    detect_fos: bool = True,
    log_prefix: str = "",
) -> ExtractionResult:
    """
    PDF extraction. When `detect_fos` is False we skip opening the PDF entirely
    (downstream parsing handles binary directly), avoiding a redundant
    pdfplumber pass on top of `probe_size`.
    """
    if not detect_fos:
        logger.info(
            "%s extract_pdf: skipping local parse (detect_fos=False) bytes=%d",
            log_prefix, len(content),
        )
        return ExtractionResult(
            raw_text=None,
            raw_bytes=content,
            is_pdf=True,
            detected_fos_name=None,
            total_pages=None,
        )

    import pdfplumber

    t0 = time.perf_counter()
    fos_name: str | None = None
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        total_pages = len(pdf.pages)
        head_lines: list[str] = []
        for page in pdf.pages[:2]:
            t = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            head_lines.extend(t.splitlines())
        fos_name = _detect_fos_in_lines(head_lines[:30])

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "%s extract_pdf: pages=%d fos_detected=%s elapsed_ms=%.0f",
        log_prefix, total_pages, bool(fos_name), elapsed_ms,
    )

    return ExtractionResult(
        raw_text=None,
        raw_bytes=content,
        is_pdf=True,
        detected_fos_name=fos_name,
        total_pages=total_pages,
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def extract_csv(content: bytes, *, log_prefix: str = "") -> ExtractionResult:
    t0 = time.perf_counter()
    decoded = content.decode("utf-8", errors="replace")
    all_rows = list(csv.reader(io.StringIO(decoded)))
    res = _tabular_to_result(all_rows)
    logger.info(
        "%s extract_csv: rows_in=%d rows_out=%d fos_detected=%s elapsed_ms=%.0f",
        log_prefix, len(all_rows), res.total_rows or 0,
        bool(res.detected_fos_name), (time.perf_counter() - t0) * 1000,
    )
    return res


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def extract_xlsx(content: bytes, *, log_prefix: str = "") -> ExtractionResult:
    import openpyxl

    t0 = time.perf_counter()
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active or wb.worksheets[0]
    all_rows: list[list[str]] = []
    for row in ws.iter_rows(values_only=True):
        all_rows.append([str(c) if c is not None else "" for c in row])
    wb.close()
    res = _tabular_to_result(all_rows)
    logger.info(
        "%s extract_xlsx: rows_in=%d rows_out=%d fos_detected=%s elapsed_ms=%.0f",
        log_prefix, len(all_rows), res.total_rows or 0,
        bool(res.detected_fos_name), (time.perf_counter() - t0) * 1000,
    )
    return res


# ---------------------------------------------------------------------------
# XLS
# ---------------------------------------------------------------------------

def extract_xls(content: bytes, *, log_prefix: str = "") -> ExtractionResult:
    import xlrd

    t0 = time.perf_counter()
    wb = xlrd.open_workbook(file_contents=content)
    ws = wb.sheet_by_index(0)
    all_rows: list[list[str]] = []
    for row_idx in range(ws.nrows):
        all_rows.append([str(ws.cell_value(row_idx, c)) for c in range(ws.ncols)])
    res = _tabular_to_result(all_rows)
    logger.info(
        "%s extract_xls: rows_in=%d rows_out=%d fos_detected=%s elapsed_ms=%.0f",
        log_prefix, len(all_rows), res.total_rows or 0,
        bool(res.detected_fos_name), (time.perf_counter() - t0) * 1000,
    )
    return res


# ---------------------------------------------------------------------------
# Tabular helper
# ---------------------------------------------------------------------------

def _tabular_to_result(all_rows: list[list[str]]) -> ExtractionResult:
    """Detect FOS name, strip trailing empty rows, return minimal CSV text."""
    header_idx = _find_header_row(all_rows)
    meta_rows = all_rows[:header_idx]
    header_row = all_rows[header_idx] if header_idx < len(all_rows) else []
    data_rows = all_rows[header_idx + 1:] if header_idx < len(all_rows) else []

    while data_rows and not any(c.strip() for c in data_rows[-1]):
        data_rows.pop()

    fos_name = _find_fos_in_rows(all_rows, header_idx)

    out = io.StringIO()
    writer = csv.writer(out)
    for row in meta_rows:
        if any(c.strip() for c in row):
            writer.writerow(row)
    if header_row:
        writer.writerow(header_row)
    for row in data_rows:
        writer.writerow(row)

    return ExtractionResult(
        raw_text=out.getvalue(),
        raw_bytes=None,
        is_pdf=False,
        detected_fos_name=fos_name,
        total_rows=len(data_rows),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _find_header_row(rows: list[list[str]]) -> int:
    for i, row in enumerate(rows[:25]):
        string_cells = [c for c in row if c.strip() and not _is_numeric(c)]
        if len(string_cells) >= 3:
            return i
    return 0


def _is_numeric(value: str) -> bool:
    try:
        float(value.replace(",", ""))
        return True
    except ValueError:
        return False


def _find_fos_in_rows(rows: list[list[str]], header_row_index: int) -> str | None:
    for row in rows[:header_row_index]:
        for i, cell in enumerate(row):
            if cell and _FOS_KEYWORDS.search(cell):
                after_colon = re.split(r"[:：]", cell, maxsplit=1)
                if len(after_colon) == 2 and after_colon[1].strip():
                    return after_colon[1].strip()
                if i + 1 < len(row) and row[i + 1].strip():
                    return row[i + 1].strip()
    return None


def _detect_fos_in_lines(lines: list[str]) -> str | None:
    for line in lines:
        if _FOS_KEYWORDS.search(line):
            after_colon = re.split(r"[:：]", line, maxsplit=1)
            if len(after_colon) == 2 and after_colon[1].strip():
                return after_colon[1].strip()
    return None


# ---------------------------------------------------------------------------
# Dispatcher + size introspection
# ---------------------------------------------------------------------------

def extract(
    filename: str,
    content: bytes,
    *,
    detect_fos: bool = True,
    log_prefix: str = "",
) -> ExtractionResult:
    ext = Path(filename).suffix.lower()
    logger.info(
        "%s extract: filename=%r ext=%s bytes=%d detect_fos=%s",
        log_prefix, filename, ext, len(content), detect_fos,
    )
    if ext == ".pdf":
        return extract_pdf(content, detect_fos=detect_fos, log_prefix=log_prefix)
    if ext == ".csv":
        return extract_csv(content, log_prefix=log_prefix)
    if ext == ".xlsx":
        return extract_xlsx(content, log_prefix=log_prefix)
    if ext == ".xls":
        return extract_xls(content, log_prefix=log_prefix)
    raise ValueError(f"Unsupported file format: {ext!r}. Supported: pdf, csv, xlsx, xls")


def probe_size(filename: str, content: bytes) -> tuple[int | None, int | None]:
    """
    Cheap size probe used by the upload endpoint to enforce limits BEFORE heavy processing.
    Returns (page_count_or_None, row_count_or_None). Callers check len(content) separately.
    """
    ext = Path(filename).suffix.lower()
    t0 = time.perf_counter()
    pages: int | None = None
    rows: int | None = None
    try:
        if ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                pages = len(pdf.pages)
        elif ext == ".csv":
            decoded = content.decode("utf-8", errors="replace")
            rows = sum(1 for _ in csv.reader(io.StringIO(decoded)))
        elif ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active or wb.worksheets[0]
            rows = ws.max_row or 0
            wb.close()
        elif ext == ".xls":
            import xlrd
            wb = xlrd.open_workbook(file_contents=content)
            rows = wb.sheet_by_index(0).nrows
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "probe_size: failed for %r (%s: %s) — returning None,None",
            filename, type(exc).__name__, exc,
        )
        return None, None
    logger.info(
        "probe_size: filename=%r ext=%s bytes=%d pages=%s rows=%s elapsed_ms=%.0f",
        filename, ext, len(content), pages, rows, (time.perf_counter() - t0) * 1000,
    )
    return pages, rows
