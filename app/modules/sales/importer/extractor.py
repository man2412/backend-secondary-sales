"""
Lightweight raw-text extraction from uploaded sales files.

Responsibility: convert bytes → plain text that the LLM can read.
No column detection, no data parsing — just faithful text representation.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import NamedTuple


class ExtractionResult(NamedTuple):
    text: str
    detected_fos_name: str | None


# Keywords that signal a FOS/MR name in spreadsheet metadata rows
_FOS_KEYWORDS = re.compile(
    r"\b(fos|fos\s*name|mr|mr\s*name|rep|representative|field\s*officer|medical\s*rep)\b",
    re.IGNORECASE,
)


def _find_fos_in_rows(rows: list[list[str]], header_row_index: int) -> str | None:
    """Scan metadata rows (above the data header) for a FOS/MR name."""
    for row in rows[:header_row_index]:
        for i, cell in enumerate(row):
            if cell and _FOS_KEYWORDS.search(cell):
                # Value is either in the next cell or after a colon in the same cell
                after_colon = re.split(r"[:：]", cell, maxsplit=1)
                if len(after_colon) == 2 and after_colon[1].strip():
                    return after_colon[1].strip()
                if i + 1 < len(row) and row[i + 1].strip():
                    return row[i + 1].strip()
    return None


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def extract_pdf(content: bytes) -> ExtractionResult:
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=3, y_tolerance=3)
            if text:
                lines.append(text)

    full_text = "\n".join(lines)

    # FOS detection in PDF: look in the first ~20 lines
    fos_name: str | None = None
    for line in full_text.splitlines()[:20]:
        if _FOS_KEYWORDS.search(line):
            after_colon = re.split(r"[:：]", line, maxsplit=1)
            if len(after_colon) == 2 and after_colon[1].strip():
                fos_name = after_colon[1].strip()
                break

    return ExtractionResult(text=full_text, detected_fos_name=fos_name)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def extract_csv(content: bytes) -> ExtractionResult:
    text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)

    # Find header row index (first row with 3+ non-empty string cells)
    header_idx = 0
    for i, row in enumerate(all_rows):
        non_empty = [c for c in row if c.strip()]
        if len(non_empty) >= 3:
            header_idx = i
            break

    fos_name = _find_fos_in_rows(all_rows, header_idx)

    # Re-emit as readable text: metadata lines first, then TSV-style data
    output_lines: list[str] = []
    for i, row in enumerate(all_rows):
        output_lines.append("\t".join(cell.strip() for cell in row))

    return ExtractionResult(text="\n".join(output_lines), detected_fos_name=fos_name)


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

    header_idx = _find_header_row(all_rows)
    fos_name = _find_fos_in_rows(all_rows, header_idx)

    output_lines = ["\t".join(row) for row in all_rows]
    return ExtractionResult(text="\n".join(output_lines), detected_fos_name=fos_name)


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

    header_idx = _find_header_row(all_rows)
    fos_name = _find_fos_in_rows(all_rows, header_idx)

    output_lines = ["\t".join(row) for row in all_rows]
    return ExtractionResult(text="\n".join(output_lines), detected_fos_name=fos_name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_header_row(rows: list[list[str]]) -> int:
    """Return index of the first row that looks like a data header (≥3 non-numeric string cells)."""
    for i, row in enumerate(rows[:20]):
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


# ---------------------------------------------------------------------------
# Dispatcher
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
