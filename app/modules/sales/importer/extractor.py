"""
Lightweight raw-text extraction from uploaded sales files.

Splits each file into chunks sized for reliable LLM processing:
  - PDFs:       5 pages per chunk
  - Excel/CSV:  80 data rows per chunk, header re-prepended to every chunk

Each chunk carries an independent row-count heuristic so the orchestrator can
detect silent drops (LLM returned 40 rows for a chunk expected to have ~78).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path


PDF_PAGES_PER_CHUNK = 5
ROWS_PER_CHUNK = 80

# Matches common distributor date formats on a line: 07/03/26, 07-03-2026, 2026-03-07, 07.03.26
_DATE_LINE_RE = re.compile(
    r"\b(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}|\d{4}[./\-]\d{1,2}[./\-]\d{1,2})\b"
)

_FOS_KEYWORDS = re.compile(
    r"\b(fos|fos\s*name|mr|mr\s*name|rep|representative|field\s*officer|medical\s*rep)\b",
    re.IGNORECASE,
)


@dataclass
class Chunk:
    index: int
    text: str
    expected_rows: int


@dataclass
class ExtractionResult:
    chunks: list[Chunk] = field(default_factory=list)
    detected_fos_name: str | None = None
    total_pages: int | None = None
    total_rows: int | None = None

    @property
    def text(self) -> str:
        """Full text (for pre-filter store/product matching)."""
        return "\n".join(c.text for c in self.chunks)

    @property
    def expected_rows_total(self) -> int:
        return sum(c.expected_rows for c in self.chunks)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def extract_pdf(content: bytes) -> ExtractionResult:
    import pdfplumber

    page_texts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            page_texts.append(t)

    total_pages = len(page_texts)

    # FOS detection: scan first ~30 lines across first 2 pages
    head = "\n".join(page_texts[:2]).splitlines()[:30]
    fos_name = _detect_fos_in_lines(head)

    chunks: list[Chunk] = []
    for chunk_idx, start in enumerate(range(0, total_pages, PDF_PAGES_PER_CHUNK)):
        page_group = page_texts[start : start + PDF_PAGES_PER_CHUNK]
        text = "\n\n--- PAGE BREAK ---\n\n".join(page_group)
        chunks.append(
            Chunk(
                index=chunk_idx,
                text=text,
                expected_rows=_count_data_rows_pdf(text),
            )
        )

    return ExtractionResult(
        chunks=chunks,
        detected_fos_name=fos_name,
        total_pages=total_pages,
    )


def _count_data_rows_pdf(text: str) -> int:
    """Heuristic: each sale row has a date somewhere on the line."""
    return sum(1 for ln in text.splitlines() if _DATE_LINE_RE.search(ln))


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def extract_csv(content: bytes) -> ExtractionResult:
    decoded = content.decode("utf-8", errors="replace")
    all_rows = list(csv.reader(io.StringIO(decoded)))
    return _chunk_tabular(all_rows)


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def extract_xlsx(content: bytes) -> ExtractionResult:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active or wb.worksheets[0]

    all_rows: list[list[str]] = []
    for row in ws.iter_rows(values_only=True):
        all_rows.append([str(c) if c is not None else "" for c in row])
    wb.close()
    return _chunk_tabular(all_rows)


# ---------------------------------------------------------------------------
# XLS
# ---------------------------------------------------------------------------

def extract_xls(content: bytes) -> ExtractionResult:
    import xlrd

    wb = xlrd.open_workbook(file_contents=content)
    ws = wb.sheet_by_index(0)

    all_rows: list[list[str]] = []
    for row_idx in range(ws.nrows):
        all_rows.append([str(ws.cell_value(row_idx, col_idx)) for col_idx in range(ws.ncols)])
    return _chunk_tabular(all_rows)


# ---------------------------------------------------------------------------
# Tabular chunking helper (used by CSV / XLSX / XLS)
# ---------------------------------------------------------------------------

def _chunk_tabular(all_rows: list[list[str]]) -> ExtractionResult:
    """Find header row, split remaining rows into 80-row chunks, re-prepend header."""
    header_idx = _find_header_row(all_rows)
    meta_rows = all_rows[:header_idx]
    header_row = all_rows[header_idx] if header_idx < len(all_rows) else []
    data_rows = all_rows[header_idx + 1 :] if header_idx < len(all_rows) else []

    # Drop trailing fully-empty rows
    while data_rows and not any(c.strip() for c in data_rows[-1]):
        data_rows.pop()

    fos_name = _find_fos_in_rows(all_rows, header_idx)

    # Meta + header context that every chunk needs
    context_lines: list[str] = []
    for row in meta_rows:
        line = "\t".join(c.strip() for c in row)
        if line.strip():
            context_lines.append(line)
    if header_row:
        context_lines.append("\t".join(c.strip() for c in header_row))
    context_block = "\n".join(context_lines)

    chunks: list[Chunk] = []
    if not data_rows:
        # No data rows, still return a single chunk with just the header so LLM can respond cleanly
        chunks.append(Chunk(index=0, text=context_block, expected_rows=0))
    else:
        for chunk_idx, start in enumerate(range(0, len(data_rows), ROWS_PER_CHUNK)):
            group = data_rows[start : start + ROWS_PER_CHUNK]
            body = "\n".join("\t".join(c.strip() for c in row) for row in group)
            chunk_text = f"{context_block}\n{body}" if context_block else body
            chunks.append(
                Chunk(
                    index=chunk_idx,
                    text=chunk_text,
                    expected_rows=_count_data_rows_tabular(group),
                )
            )

    return ExtractionResult(
        chunks=chunks,
        detected_fos_name=fos_name,
        total_rows=len(data_rows),
    )


def _count_data_rows_tabular(rows: list[list[str]]) -> int:
    """Count rows that look like real data (have some text AND some numeric-looking cell)."""
    count = 0
    for row in rows:
        has_text = any(c.strip() and not _is_numeric(c) for c in row)
        has_num = any(c.strip() and _is_numeric(c) for c in row)
        if has_text and has_num:
            count += 1
    return count


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

def extract(filename: str, content: bytes) -> ExtractionResult:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return extract_pdf(content)
    if ext == ".csv":
        return extract_csv(content)
    if ext == ".xlsx":
        return extract_xlsx(content)
    if ext == ".xls":
        return extract_xls(content)
    raise ValueError(f"Unsupported file format: {ext!r}. Supported: pdf, csv, xlsx, xls")


def probe_size(filename: str, content: bytes) -> tuple[int | None, int | None]:
    """
    Cheap size probe used by the upload endpoint to enforce limits BEFORE heavy processing.
    Returns (page_count_or_None, row_count_or_None). Size caller can check len(content) itself.
    """
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                return len(pdf.pages), None
        if ext == ".csv":
            decoded = content.decode("utf-8", errors="replace")
            return None, sum(1 for _ in csv.reader(io.StringIO(decoded)))
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active or wb.worksheets[0]
            n = ws.max_row or 0
            wb.close()
            return None, n
        if ext == ".xls":
            import xlrd
            wb = xlrd.open_workbook(file_contents=content)
            return None, wb.sheet_by_index(0).nrows
    except Exception:
        pass
    return None, None
