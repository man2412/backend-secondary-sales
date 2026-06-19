"""
LLM-based parser — Approach H: MinerU → markdown → Gemini text prompt.

For PDFs (when MINERU_API_KEY is set):
  1. MinerU cloud API converts the PDF to clean markdown (handles messy layouts,
     tables, OCR). No Gemini Files API upload — PDF binary never leaves the thread.
  2. The markdown is sent to Gemini as a plain text prompt (fast, cheap).

For PDFs (when MINERU_API_KEY is NOT set):
  Falls back to Gemini Files API binary upload (original behaviour).

For tabular (CSV / XLSX / XLS):
  Already returned as text by extractor.py — sent directly as text prompt.
  MinerU is not used for tabular files.

The LLM extracts raw name strings only. Entity→UUID resolution is done
entirely on the backend using difflib fuzzy matching (see import_service.py).

Primary model : gemini-2.5-flash-lite
Fallback model: gpt-4o-mini
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output column contract (array-of-arrays from LLM → dicts in parse_with_llm)
# ---------------------------------------------------------------------------

_COLUMNS = [
    "product_name_raw",   # 0  exact product name string from the file
    "sale_date",          # 1  YYYY-MM-DD (null if the file has no per-row date)
    "sale_qty",           # 2  integer — quantity SOLD (not "Total Qty")
    "free_qty",           # 3  integer (0 if absent)
    "mrp",                # 4  decimal
    "ptr",                # 5  decimal Rate / selling price
    "reported_amount",    # 6  decimal Amount / Value / Sale-value total
    "bill_ref",           # 7  bill/invoice reference string
    "batch",              # 8  batch number
    "pack",               # 9  pack size/form  e.g. "1X10TA"
    "customer_name_raw",  # 10 exact party/store name from file
    "mr_name_raw",        # 11 FOS/MR/SalesMan/TERRITORY name (null if absent)
    "doctor_name_raw",    # 12 doctor name (null / "GENERAL" → null)
    "free_value_raw",     # 13 value of free goods if a separate column exists
]

# The LLM now returns named-key OBJECTS (not positional arrays), which removes the
# whole class of "value landed in the wrong slot" bugs. This maps the model's keys
# (and a few common aliases / the internal names themselves) → internal fields.
_KEY_ALIASES = {
    "product_name": "product_name_raw", "product": "product_name_raw",
    "item": "product_name_raw", "product_name_raw": "product_name_raw",
    "sale_date": "sale_date", "date": "sale_date",
    "sale_qty": "sale_qty", "qty": "sale_qty", "quantity": "sale_qty", "sales": "sale_qty",
    "free_qty": "free_qty", "free": "free_qty", "f_qty": "free_qty",
    "mrp": "mrp",
    "rate": "ptr", "ptr": "ptr", "sale_rate": "ptr",
    "amount": "reported_amount", "value": "reported_amount",
    "sale_value": "reported_amount", "reported_amount": "reported_amount",
    "free_value": "free_value_raw", "fr_value": "free_value_raw",
    "free_amount": "free_value_raw", "free_value_raw": "free_value_raw",
    "bill_ref": "bill_ref", "bill": "bill_ref", "bill_no": "bill_ref",
    "invoice": "bill_ref", "inv_no": "bill_ref",
    "batch": "batch", "batch_no": "batch",
    "pack": "pack", "packing": "pack",
    "customer_name": "customer_name_raw", "customer": "customer_name_raw",
    "party": "customer_name_raw", "customer_name_raw": "customer_name_raw",
    "mr_name": "mr_name_raw", "mr": "mr_name_raw", "fso": "mr_name_raw",
    "fso_name": "mr_name_raw", "territory": "mr_name_raw", "mr_name_raw": "mr_name_raw",
    "doctor_name": "doctor_name_raw", "doctor": "doctor_name_raw",
    "dr_name": "doctor_name_raw", "doctor_name_raw": "doctor_name_raw",
}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LLMParseRequest:
    raw_text: str | None = None        # tabular CSV text
    pdf_bytes: bytes | None = None     # PDF raw bytes → Files API
    is_pdf: bool = False
    detected_fos_name: str | None = None
    # Carried purely for debug logging — has no effect on parsing.
    log_prefix: str = ""


@dataclass
class LLMParseResponse:
    rows: list[dict] = field(default_factory=list)
    model_used: str = ""
    raw_response: dict = field(default_factory=dict)
    # Per-chunk failure messages when chunking is used. Surfaced to the user
    # as `extraction_warnings` on the import job so they know which chunk(s)
    # produced no rows.
    warnings: list[str] = field(default_factory=list)
    # Month the report covers (YYYY-MM), read by the LLM from the title/header.
    # Used to fill sale_date for rows that have no per-row date.
    report_month: str | None = None


# ---------------------------------------------------------------------------
# Shared system prompt  (no entity lists — kept intentionally compact)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a data extraction assistant for a pharmaceutical CRM. You are given one
distributor's secondary-sales report in SOME layout — an Excel/CSV sheet rendered
as text, or a PDF rendered as markdown / HTML tables. Column names, column order,
header row position, and overall structure VARY by distributor and even month to
month for the same distributor. Read the content intelligently and map it to the
fixed schema below. Never assume a specific column order — identify each column by
its header text and the kind of values beneath it.

Return JSON: {"report_month": "YYYY-MM" | null, "rows": [ {row-object}, ... ]}

report_month — the month the report covers, as YYYY-MM. Read it from the title or
header text (e.g. "From: 01/01/2026 To: 31/01/2026", "JAN-26", "Sales Report Jan
2026"). It is used as the sale month for rows that have no own date. null only if
truly indeterminable.

Each row is a JSON OBJECT keyed by FIELD NAME (not position). Include a key only
when the file has a value for it; omit it or use null otherwise. Because values
are keyed by name, map each column to its MEANING regardless of the file's column
order or how many columns it has — a value can never go in the "wrong slot".
Keys:
  "product_name"  the specific drug / ITEM name (e.g. "APTIGLIM M1 PR TAB"),
                  NOT a manufacturer / company / division name (e.g.
                  "APTUS CD CARE", "… DIVI", "MF: …"). If a row carries BOTH a
                  manufacturer/division and an item, use the ITEM. If a
                  continuation row leaves it blank but clearly belongs to the
                  product above (different batch / invoice), carry it down.
  "sale_date"     that row's date as YYYY-MM-DD. Omit if the file has NO per-row
                  date column (many monthly summaries don't). Do NOT invent one.
  "sale_qty"      quantity SOLD (paid). Synonyms: Qty, Sale Qty, SaleQty, Sales.
                  Numeric; NEGATIVE for sale-returns; MAY be fractional (e.g. 2.5).
                  This is NOT "Total Qty" (= sale + free) and NOT the free column.
                  DISAMBIGUATION: if there are two quantity-like columns, ARITHMETIC
                  decides — the column whose value × rate ≈ amount is sale_qty; the
                  other is free_qty. This OVERRIDES the column's name (e.g. a
                  "Qty=10, S.Qty=2, Rate=37.5, Amount=375" row → sale_qty=10,
                  free_qty=2, because 10×37.5=375).
  "free_qty"      FREE / scheme quantity. Synonyms: Free, F.Qty, Fqty, F. Qty, Fee,
                  Sch Qty, S. Qty, FREE QTY. 0 if absent. May be fractional (0.5).
  "mrp"           MRP (per-unit price). null if absent.
  "rate"          selling rate per unit. Synonyms: Rate, Sale Rate, S. Rate, PTR.
  "amount"        the sale LINE total (≈ qty × rate). Synonyms: Amount, Value, Sale
                  Amount, NetSales, FinalAmt, Total Value. This is NOT a per-unit
                  price — keep it separate from "mrp" and "rate". A file with a
                  single money column maps it to "amount".
  "free_value"    value of the free goods, only if a separate column exists.
                  Synonyms: Fr.Value, Free Amount, FrQty Amt.
  "bill_ref"      bill / invoice / challan no. Synonyms: BillRef, InvNo, Bill No.
  "batch"         batch / lot no.
  "pack"          pack size/form e.g. "10 TAB", "1X10".
  "customer_name" the PARTY / medical store / dealer the sale is to.
  "mr_name"       field officer / MR / sales rep. Synonyms: FSO, Fso name, MR, Rep,
                  SalesMan, TERRITORY.
  "doctor_name"   the doctor. Synonyms: Dr name, DR NAME, RXBER. "GENERAL" → null.

Example row object:
  {"product_name": "APTIGLIM M2 SR TAB", "sale_qty": 5, "free_qty": 1,
   "rate": 64.18, "amount": 320.9, "bill_ref": "CA-T/28305", "batch": "CGX03AGA",
   "pack": "10 TAB", "customer_name": "SHIV MEDICARE", "sale_date": "2026-01-15"}

Structure rules — handle ALL of these layouts:
- The customer/party may be (a) an inline column on every row, OR (b) a SECTION
  HEADER that introduces a block — e.g. "Party: ABC STORE [CITY]", a standalone
  bold name line, a `<td colspan=...>NAME, CITY</td>` row, OR (very common in
  spreadsheets) a row whose FIRST column holds a store/party name while the
  Qty/Free/Amount columns are EMPTY (often with just a city/area in another
  column, e.g. "DAWA ZONE MEDICAL & FOODS , , , JAMNAGAR"). Such a name-only row
  is a CUSTOMER section header — NOT a product — and its customer_name applies to
  EVERY following data row until the next section header. Each product line under
  it (which DOES have a qty/amount) must carry that customer_name.
  If the section header spans MULTIPLE lines — a party NAME line followed by an
  ADDRESS line (street / road / building / room / plot / shop-no / city, e.g.
  "MKT-2303.ROOM-1.…, COLLEGE RD.BILIMOR") — use the NAME line as customer_name
  and IGNORE the address line. The name often carries a leading account code
  (e.g. "3619 KAIZENS …"); keep the full name line as-is, code included.
- mr_name and doctor_name may likewise appear once on a section row or a
  "Party Total" row rather than on each data row — apply them to that party's rows.
- One cell may merge two fields (e.g. "MRP Batch" = "84.23 CGX03"; or "Qty Free" /
  "Rate Amount" sharing one header) — split on whitespace into the correct columns.
- SKIP non-transaction rows: "Party Total", "Grand Total", "Page Total",
  "1. Invoice", "Sum of …", "Row Labels", and manufacturer/company section rows
  ("MF : …", a company name with no quantities).
- Input may contain multiple sheets separated by a "### SHEET: <name>" marker —
  extract from every sheet.
- Parse all date formats to YYYY-MM-DD (D/M/YYYY, DD-MM-YYYY, etc.). Numbers may
  use comma thousand-separators — parse as plain decimals.
- Negative quantities = sale-returns (bill refs often contain "SR") — keep them
  negative; do NOT drop or flip them.
- A file may have FEWER columns than these keys (e.g. only Item, Qty, F.Qty,
  Amount). Just emit the keys you have values for and omit the rest — order and
  count don't matter since rows are keyed by name.
- Do NOT invent data. Only extract what is present; leave unknown fields null.\
"""


# ---------------------------------------------------------------------------
# MinerU  — PDF → clean markdown (eliminates Gemini Files API upload)
# ---------------------------------------------------------------------------

_MINERU_TIMEOUT_S = 300


def _call_mineru_extract(pdf_bytes: bytes, *, log_prefix: str = "") -> str:
    """
    Convert a PDF to clean markdown using the MinerU cloud API.

    The SDK expects a file path, so the bytes are written to a temp file,
    extracted, then the temp file is deleted. The call is blocking and is
    always invoked inside asyncio.to_thread (via parse_with_llm).

    Raises RuntimeError if MinerU returns empty content, triggering the
    Gemini Files API fallback in parse_with_llm.
    """
    from mineru import MinerU  # type: ignore

    logger.info(
        "%s mineru: starting pdf_bytes=%d", log_prefix, len(pdf_bytes),
    )
    t0 = time.perf_counter()
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name

        with MinerU(settings.MINERU_API_KEY) as client:
            result = client.extract(
                tmp_path,
                language="en",
                table=True,   # ensure tables are extracted properly
                timeout=_MINERU_TIMEOUT_S,
            )

        markdown = (result.markdown or "").strip()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if not markdown:
            logger.warning(
                "%s mineru: empty markdown returned (elapsed_ms=%.0f, err_code=%r, error=%r)",
                log_prefix, elapsed_ms,
                getattr(result, "err_code", None),
                getattr(result, "error", None),
            )
            raise RuntimeError("MinerU returned empty markdown for this PDF")
        logger.info(
            "%s mineru: ok markdown_chars=%d elapsed_ms=%.0f",
            log_prefix, len(markdown), elapsed_ms,
        )
        # Log the first 40 lines so we can diagnose the exact markdown
        # structure and verify the chunking splitter will work correctly.
        first_lines = markdown.splitlines()[:40]
        logger.info(
            "%s mineru: markdown_preview (first %d lines):\n%s",
            log_prefix, len(first_lines),
            "\n".join(f"  {i:3}: {ln}" for i, ln in enumerate(first_lines)),
        )
        return markdown

    except Exception:
        logger.exception(
            "%s mineru: call failed after elapsed_ms=%.0f",
            log_prefix, (time.perf_counter() - t0) * 1000,
        )
        raise

    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Markdown chunking — split large MinerU markdown into N parallel chunks
# ---------------------------------------------------------------------------
#
# Three PDF output structures from MinerU, all handled:
#
#  Structure A — "section-per-customer" (structured PDF):
#    ## Customer: ABC Pharmacy
#    | Product | Qty | ...
#    ## Customer: XYZ Drug
#    | Product | Qty | ...
#    → Strategy 1: split at `#` heading boundaries.
#
#  Structure B — "HTML page-table" (distributor reports, most common in prod):
#    MinerU emits ONE `<table>...</table>` per PDF page. Each table is on a
#    single (very long) line and is self-contained: first <tr> is the column-
#    header row, subsequent <tr><td colspan="9">NAME</td></tr> rows mark
#    customer sections, and data rows follow. Customers that span pages are
#    safe to split: the customer-section header row is repeated at the top
#    of every continuation page's table.
#    → Strategy 2a: split between adjacent `<table>` blocks.
#
#  Structure C — "markdown pipe-table per page":
#    Each page table starts with a column-header row + |---| separator,
#    then customer-section rows (colspan → | CUSTOMER NAME | | | | ... |)
#    and data rows.
#    → Strategy 2b: split at pipe-table page boundaries.
#
# Strategy 3 (safe line-count fallback) cuts only at blank lines or after
# </table> — never mid-table. Strategy 4 is the naive char-balanced fallback.

def _balance_boundaries(
    boundary_candidates: list[int],
    n: int,
    cumulative_chars: list[int],
) -> list[int]:
    """
    Given a sorted list of safe cut indices (each pointing to a line where a new
    section begins), pick `n - 1` boundaries that split the document into n
    roughly equal chunks by character count.

    `cumulative_chars[i]` is the total chars from line 0 up to (but not
    including) line `i`. This lets us compute the char-position of any
    candidate boundary in O(1) and pick the candidates closest to the
    ideal char-position targets (total_chars/n, 2*total_chars/n, ...).

    Picking boundaries by char-position rather than by ordinal position
    in the candidate list is critical when section sizes are uneven
    (e.g. some PDF pages have 30+ sale rows, others have 3).
    """
    if n <= 1 or not boundary_candidates:
        return []
    total_chars = cumulative_chars[-1] if cumulative_chars else 0
    if total_chars <= 0:
        return []
    targets = [(m * total_chars) // n for m in range(1, n)]
    chosen: list[int] = []
    used: set[int] = set()
    cand_positions = [cumulative_chars[b] for b in boundary_candidates]
    for t in targets:
        # Find the candidate boundary whose char-position is closest to t,
        # skipping any already-used boundary (so we never pick the same one
        # twice when n > number of candidate gaps).
        best_idx = -1
        best_diff = float("inf")
        for i, pos in enumerate(cand_positions):
            if i in used:
                continue
            diff = abs(pos - t)
            if diff < best_diff:
                best_diff = diff
                best_idx = i
        if best_idx < 0:
            break
        used.add(best_idx)
        chosen.append(boundary_candidates[best_idx])
    return sorted(set(chosen))


def _split_markdown_into_chunks(text: str, n: int) -> list[str]:
    """
    Split MinerU markdown into `n` safe parallel chunks.

    Strategy (tried in order — stops at first that works):

    1. Heading split: split at `#` boundaries. For section-per-customer PDFs
       where MinerU emits one `##` heading per customer. Requires ≥ n-1
       heading boundaries after line 0.

    2a. HTML page-table split: MinerU sometimes emits one `<table>...</table>`
        per PDF page (typical for distributor reports rendered as HTML tables).
        Split between adjacent `<table>` blocks so every chunk starts cleanly
        at a `<table>` opener and ends at a `</table>` closer — never inside
        a table. This is the safest and highest-priority strategy for HTML
        output because each `<table>` is self-contained (column headers +
        customer-section rows are repeated at the top of every page table).

    2b. Markdown pipe-table split: for distributor reports where MinerU emits
        one markdown pipe-table per PDF page. Detects each table-start
        (column-header row + |---| separator) and splits at page boundaries.

    3. Line-count split with safe boundaries: cut between logical blocks
       (after `</table>` or after a blank line) at positions closest to the
       ideal char-targets. Never cuts mid-table or mid-row.

    4. Single call: if nothing safe is found, return [text]. Accuracy
       preserved; the caller uses single-call mode.
    """
    if n <= 1 or not text:
        return [text] if text else []

    lines = text.splitlines(keepends=True)
    if len(lines) < n * 2:
        return [text]

    # Pre-compute cumulative char positions so any strategy can pick boundaries
    # by char-position (which respects uneven section sizes) in O(1) per pick.
    cum: list[int] = [0]
    for ln in lines:
        cum.append(cum[-1] + len(ln))

    def _emit(boundaries: list[int]) -> list[str]:
        out: list[str] = []
        prev = 0
        for b in boundaries:
            out.append("".join(lines[prev:b]))
            prev = b
        out.append("".join(lines[prev:]))
        return [c for c in out if c.strip()]

    # --- Strategy 1: heading-based split ---
    heading_idxs = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("#")]
    boundary_candidates = [i for i in heading_idxs if i > 0]
    if len(boundary_candidates) >= n - 1:
        boundaries = _balance_boundaries(boundary_candidates, n, cum)
        chunks = _emit(boundaries)
        if len(chunks) >= 2:
            logger.info(
                "split: strategy1 heading produced %d chunks (boundaries=%d candidates)",
                len(chunks), len(boundary_candidates),
            )
            return chunks

    # --- Strategy 2a: HTML <table> page-boundary split ---
    #
    # MinerU often emits ONE HTML `<table>...</table>` per PDF page, with each
    # table on its own (very long) line. Every page table is self-contained:
    #
    #   <table><tr><td>Product</td><td>Pack</td><td>BillRef</td><td>Date</td>...
    #          <tr><td colspan="9">CUSTOMER NAME, CITY, CITY</td></tr>
    #          <tr><td>PRODUCT</td><td>10 TAB</td><td>CA-T/12345</td><td>5/2/2026</td>...
    #          ...
    #   </table>
    #
    # Customer sections that span pages are safe to split between: MinerU
    # repeats the customer-section header row at the top of every continuation
    # page's table, so every chunk gets full column-header + customer context.
    #
    # Safe boundary = the FIRST line of any `<table>` block AFTER the very first
    # one. Cutting there means the previous chunk ends with `</table>` (plus
    # trailing blanks/metadata) and the next chunk begins with `<table>...`.
    html_table_starts: list[int] = []
    for i, ln in enumerate(lines):
        # Strip leading whitespace and a possible "  NN: " line-number prefix
        # (MinerU markdown preview format) — but here we treat plain content.
        if "<table" in ln.lower():
            html_table_starts.append(i)
    if len(html_table_starts) >= n:
        # Candidate boundaries: every <table>-start after the very first one.
        # That guarantees chunk 1 contains at least one full table and every
        # later chunk also starts with a fresh <table>.
        candidates = html_table_starts[1:]
        if len(candidates) >= n - 1:
            boundaries = _balance_boundaries(candidates, n, cum)
            chunks = _emit(boundaries)
            if len(chunks) >= 2:
                logger.info(
                    "split: strategy2a HTML <table> produced %d chunks "
                    "(html_tables=%d candidates=%d)",
                    len(chunks), len(html_table_starts), len(candidates),
                )
                return chunks

    # --- Strategy 2b: markdown pipe-table page-boundary split ---
    def _is_sep(ln: str) -> bool:
        s = ln.strip()
        return (
            s.startswith("|")
            and bool(s)
            and all(c in "|-: \t" for c in s)
            and "-" in s
        )

    table_start_idxs: list[int] = []
    k = 0
    while k < len(lines):
        ln = lines[k]
        stripped = ln.strip()
        if stripped.startswith("|") and not _is_sep(ln):
            j = k + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _is_sep(lines[j]):
                table_start_idxs.append(k)
                k = j + 1
                continue
        k += 1

    logger.info(
        "split: strategy2b pipe-table found %d boundaries (need >= %d)",
        len(table_start_idxs), n,
    )
    if len(table_start_idxs) >= n:
        candidates = table_start_idxs[1:]
        if len(candidates) >= n - 1:
            boundaries = _balance_boundaries(candidates, n, cum)
            chunks = _emit(boundaries)
            if len(chunks) >= 2:
                logger.info(
                    "split: strategy2b pipe-table produced %d chunks",
                    len(chunks),
                )
                return chunks

    # --- Strategy 3: line-count split at SAFE boundaries only ---
    #
    # Structure-aware strategies all failed. Fall back to char-count balancing,
    # but only cut at SAFE line boundaries — never mid-table:
    #   - after a line ending with `</table>` (HTML table end)
    #   - after a blank line that is preceded by data (paragraph break)
    #
    # This avoids the failure mode where strategy3 would otherwise put the
    # END of a table in chunk N and the START of the next table in chunk N+1
    # with no usable context in between.
    safe_boundary_idxs: list[int] = []
    for i, ln in enumerate(lines):
        if i == 0:
            continue
        prev = lines[i - 1]
        # boundary BEFORE line `i` is safe when prev is a structural close
        if "</table>" in prev.lower():
            safe_boundary_idxs.append(i)
            continue
        # …or when prev is blank AND we're not at the very top of the doc
        if prev.strip() == "" and i > 1:
            safe_boundary_idxs.append(i)

    if len(safe_boundary_idxs) >= n - 1:
        boundaries = _balance_boundaries(safe_boundary_idxs, n, cum)
        chunks = _emit(boundaries)
        if len(chunks) >= 2:
            logger.info(
                "split: strategy3 safe-line-boundary produced %d chunks "
                "(safe_boundaries=%d total_chars=%d)",
                len(chunks), len(safe_boundary_idxs), cum[-1],
            )
            return chunks

    # --- Strategy 4: naive line-count split (last-resort) ---
    # Only used if even safe boundaries aren't available. May cut between any
    # two adjacent lines, but never mid-line.
    total_chars = cum[-1]
    target = total_chars // n
    chunks_fb: list[str] = []
    buf: list[str] = []
    buf_chars = 0
    part = 0
    for ln in lines:
        buf.append(ln)
        buf_chars += len(ln)
        if buf_chars >= target and part < n - 1:
            chunks_fb.append("".join(buf))
            buf = []
            buf_chars = 0
            part += 1
    if buf:
        chunks_fb.append("".join(buf))
    chunks_fb = [c for c in chunks_fb if c.strip()]
    if len(chunks_fb) >= 2:
        logger.warning(
            "split: strategy4 naive line-count produced %d chunks from %d chars "
            "— no safe boundaries available, may split between non-structural lines",
            len(chunks_fb), total_chars,
        )
        return chunks_fb

    return [text]


# ---------------------------------------------------------------------------
# Row deduplication — composite key on the natural identity of a sale row
# ---------------------------------------------------------------------------
#
# Used after combining rows from parallel chunks. A sale row is uniquely
# identified by (sale_date, customer, product, sale_qty, bill_ref, batch, mrp).
#
# `batch` and `mrp` MUST be part of the key: a single invoice (bill_ref) can
# legitimately contain multiple lines for the same product/qty when different
# stock lots are dispatched together, e.g.:
#
#   APTIVOG MV 2  CA-T/33038  27/2/2026  114.04 ST25-3519  1  ...
#   APTIVOG MV 2  CA-T/33038  27/2/2026  121.65 ST25-2194  1  ...
#
# Without `batch` in the key, the two rows above would collapse into one.

def _dedupe_rows(raw_rows: list) -> tuple[list, int]:
    """
    Returns (unique_rows, removed_count). Order-stable; first occurrence wins.

    Conservatism: we ONLY drop a duplicate when every component of the
    composite key is non-empty. A row missing any key component (e.g. the
    LLM didn't catch the bill_ref) is always kept — better to surface a
    duplicate the user can review than to silently merge two legitimate
    rows that happen to share product/customer/date/qty.

    Composite key = (sale_date, customer, product, sale_qty, bill_ref,
                     batch, mrp).
    """
    seen: set[tuple] = set()
    unique: list = []

    def _norm(val: object) -> str:
        return str(val).strip() if val is not None else ""

    for row in raw_rows:
        if isinstance(row, (list, tuple)):
            # _COLUMNS index map: 0 product, 1 sale_date, 2 sale_qty,
            # 4 mrp, 7 bill_ref, 8 batch, 10 customer_name
            key = (
                _norm(row[1]) if len(row) > 1 else "",
                _norm(row[10]) if len(row) > 10 else "",
                _norm(row[0]) if len(row) > 0 else "",
                _norm(row[2]) if len(row) > 2 else "",
                _norm(row[7]) if len(row) > 7 else "",
                _norm(row[8]) if len(row) > 8 else "",
                _norm(row[4]) if len(row) > 4 else "",
            )
        elif isinstance(row, dict):
            key = (
                _norm(row.get("sale_date")),
                _norm(row.get("customer_name_raw") or row.get("customer_name")),
                _norm(row.get("product_name_raw") or row.get("product_name")),
                _norm(row.get("sale_qty")),
                _norm(row.get("bill_ref")),
                _norm(row.get("batch")),
                _norm(row.get("mrp")),
            )
        else:
            unique.append(row)
            continue

        # Conservative: only consider deduping when the IDENTITY components
        # are all populated. We require date/customer/product/qty/bill_ref to
        # be present; batch/mrp may be empty (older imports without batch
        # info) and are then ignored for the uniqueness check. This still
        # keeps APTIVOG-style multi-batch rows distinct as long as the LLM
        # extracted the batch for at least one of them.
        identity_required = key[:5]
        if not all(identity_required):
            unique.append(row)
            continue

        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    return unique, len(raw_rows) - len(unique)


# ---------------------------------------------------------------------------
# Gemini  (primary) — text prompt (post-MinerU) or Files API fallback
# ---------------------------------------------------------------------------

_GEMINI_MODEL = "gemini-2.5-flash-lite"
_GEMINI_TIMEOUT_S = 300
_GEMINI_MAX_ATTEMPTS = 3
_GEMINI_BACKOFF_SECONDS = (2, 8, 32)
_MAX_OUTPUT_TOKENS = 65536


def _is_transient_gemini_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    return name in {
        "ResourceExhausted",
        "DeadlineExceeded",
        "ServiceUnavailable",
        "InternalServerError",
        "Aborted",
        "Unknown",
        "JSONDecodeError",
    }


def _call_gemini(req: LLMParseRequest) -> tuple[dict, str]:
    import google.generativeai as genai  # type: ignore

    log_prefix = req.log_prefix
    t_total = time.perf_counter()

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=_GEMINI_MODEL,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.0,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        ),
    )

    fos_hint = (
        f"\n\nNote: the detected MR/FOS name for this file is '{req.detected_fos_name}'."
        if req.detected_fos_name
        else ""
    )

    input_mode = "pdf-files-api" if (req.is_pdf and req.pdf_bytes) else "text"
    input_size = len(req.pdf_bytes or b"") if req.is_pdf else len(req.raw_text or "")
    logger.info(
        "%s gemini: starting model=%s input_mode=%s input_size=%d",
        log_prefix, _GEMINI_MODEL, input_mode, input_size,
    )

    # Upload PDF ONCE before the retry loop and reuse the same Files API handle
    # across attempts — re-uploading 10MB+ on every transient failure was a
    # major source of latency.
    file_obj = None
    try:
        if req.is_pdf and req.pdf_bytes:
            t_upload = time.perf_counter()
            file_obj = genai.upload_file(
                io.BytesIO(req.pdf_bytes),
                mime_type="application/pdf",
            )
            logger.info(
                "%s gemini: files-api upload ok elapsed_ms=%.0f",
                log_prefix, (time.perf_counter() - t_upload) * 1000,
            )
            user_parts = [
                file_obj,
                f"Extract all sale rows from the attached distributor report PDF.{fos_hint}",
            ]
        else:
            user_parts = [
                f"Extract all sale rows from the following distributor report.{fos_hint}\n\n"
                + (req.raw_text or ""),
            ]

        last_exc: Exception | None = None
        for attempt in range(_GEMINI_MAX_ATTEMPTS):
            t_attempt = time.perf_counter()
            try:
                response = model.generate_content(
                    user_parts,
                    request_options={"timeout": _GEMINI_TIMEOUT_S},
                )
                attempt_ms = (time.perf_counter() - t_attempt) * 1000
                response_text = response.text or ""
                parsed = json.loads(response_text)
                rows_n = len(parsed.get("rows", [])) if isinstance(parsed, dict) else 0
                logger.info(
                    "%s gemini: attempt %d/%d ok response_chars=%d rows=%d "
                    "attempt_ms=%.0f total_ms=%.0f",
                    log_prefix, attempt + 1, _GEMINI_MAX_ATTEMPTS,
                    len(response_text), rows_n, attempt_ms,
                    (time.perf_counter() - t_total) * 1000,
                )
                return parsed, _GEMINI_MODEL

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                attempt_ms = (time.perf_counter() - t_attempt) * 1000
                if attempt + 1 < _GEMINI_MAX_ATTEMPTS and _is_transient_gemini_error(exc):
                    delay = _GEMINI_BACKOFF_SECONDS[attempt]
                    logger.warning(
                        "%s gemini: attempt %d/%d failed (%s: %s) "
                        "attempt_ms=%.0f — retrying in %ds",
                        log_prefix, attempt + 1, _GEMINI_MAX_ATTEMPTS,
                        type(exc).__name__, exc, attempt_ms, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.warning(
                        "%s gemini: attempt %d/%d failed (%s: %s) "
                        "attempt_ms=%.0f — no more retries",
                        log_prefix, attempt + 1, _GEMINI_MAX_ATTEMPTS,
                        type(exc).__name__, exc, attempt_ms,
                    )
                    break

        assert last_exc is not None
        raise last_exc

    finally:
        if file_obj is not None:
            try:
                file_obj.delete()
            except Exception:
                logger.warning(
                    "%s gemini: failed to delete uploaded file (ignored)",
                    log_prefix,
                )


# ---------------------------------------------------------------------------
# Gemini chunked — parallel calls over markdown slices
# ---------------------------------------------------------------------------

def _call_gemini_chunked(
    chunks: list[str],
    *,
    detected_fos_name: str | None,
    log_prefix: str,
) -> tuple[list, list[str]]:
    """
    Invoke Gemini in parallel for each markdown chunk and combine the row
    arrays. Returns (combined_raw_rows, chunk_warnings).

    Each chunk gets its own retry loop. If a chunk fails after retries, its
    error is recorded as a warning and execution continues — the user still
    gets rows from the chunks that succeeded. If ALL chunks fail, the first
    exception is re-raised so the OpenAI fallback path can take over.
    """
    import google.generativeai as genai  # type: ignore

    # Configure once for all parallel callers — global state, idempotent.
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=_GEMINI_MODEL,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.0,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        ),
    )

    fos_hint = (
        f"\n\nNote: the detected MR/FOS name for this file is '{detected_fos_name}'."
        if detected_fos_name
        else ""
    )
    n = len(chunks)

    def _do_one(idx: int, chunk_text: str) -> tuple[int, list, str | None, Exception | None]:
        chunk_prefix = f"{log_prefix}[c{idx + 1}/{n}]"
        # Log first/last 200 chars so we can see exactly what each chunk
        # contains and verify the splitter cut at safe boundaries.
        head = chunk_text[:200].replace("\n", "\\n")
        tail = chunk_text[-200:].replace("\n", "\\n")
        logger.info(
            "%s chunk_preview chars=%d head=%r tail=%r",
            chunk_prefix, len(chunk_text), head, tail,
        )
        user_part = (
            f"Extract all sale rows from the following distributor report "
            f"(this is part {idx + 1} of {n}, sent in parallel — extract only "
            f"what is in THIS part; do not invent rows from other parts).\n\n"
            "IMPORTANT for chunked input:\n"
            "- Each `<table>` in this chunk is self-contained: its first `<tr>` "
            "is the column-header row — use it to know which `<td>` is Date, Qty, etc.\n"
            "- A `<tr>` whose only cell is `<td colspan=\"9\">NAME</td>` is the "
            "customer-section header — apply that customer name to every data row "
            "until another colspan row appears, or until the table ends.\n"
            "- This chunk may begin with text BEFORE the first `<table>` (page "
            "metadata like \"Normal\", \"From: ... To: ...\", or a standalone "
            "customer name line). That text is just context — do not emit rows for it.\n"
            "- If the chunk's first `<table>` has no leading customer-section "
            "header inside it, use the standalone customer name line that appears "
            "in the text immediately before that `<table>` as the customer name.\n"
            f"{fos_hint}\n\n{chunk_text}"
        )
        last_exc: Exception | None = None
        t_chunk = time.perf_counter()
        for attempt in range(_GEMINI_MAX_ATTEMPTS):
            t_attempt = time.perf_counter()
            try:
                response = model.generate_content(
                    [user_part],
                    request_options={"timeout": _GEMINI_TIMEOUT_S},
                )
                attempt_ms = (time.perf_counter() - t_attempt) * 1000
                response_text = response.text or ""
                parsed = json.loads(response_text)
                rows = parsed.get("rows", []) if isinstance(parsed, dict) else []
                if not isinstance(rows, list):
                    rows = []
                month = parsed.get("report_month") if isinstance(parsed, dict) else None
                logger.info(
                    "%s gemini ok response_chars=%d rows=%d "
                    "attempt_ms=%.0f total_ms=%.0f",
                    chunk_prefix, len(response_text), len(rows),
                    attempt_ms, (time.perf_counter() - t_chunk) * 1000,
                )
                return idx, rows, month, None

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                attempt_ms = (time.perf_counter() - t_attempt) * 1000
                if attempt + 1 < _GEMINI_MAX_ATTEMPTS and _is_transient_gemini_error(exc):
                    delay = _GEMINI_BACKOFF_SECONDS[attempt]
                    logger.warning(
                        "%s gemini attempt %d/%d failed (%s: %s) "
                        "attempt_ms=%.0f — retrying in %ds",
                        chunk_prefix, attempt + 1, _GEMINI_MAX_ATTEMPTS,
                        type(exc).__name__, exc, attempt_ms, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.warning(
                        "%s gemini attempt %d/%d failed (%s: %s) "
                        "attempt_ms=%.0f — no more retries",
                        chunk_prefix, attempt + 1, _GEMINI_MAX_ATTEMPTS,
                        type(exc).__name__, exc, attempt_ms,
                    )
                    break

        return idx, [], None, last_exc

    logger.info(
        "%s gemini-chunked: starting n=%d sizes=%s",
        log_prefix, n, [len(c) for c in chunks],
    )
    t_total = time.perf_counter()
    # Stagger chunk submissions by 2 seconds each to avoid simultaneous
    # bursts that trigger Gemini 503 rate-limit / overload responses.
    _CHUNK_STAGGER_S = 2
    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = []
        for i, c in enumerate(chunks):
            if i > 0:
                time.sleep(_CHUNK_STAGGER_S)
            futures.append(ex.submit(_do_one, i, c))
        results = [f.result() for f in futures]

    # Stable order (chunk 1 rows come before chunk 2 rows in the combined list).
    results.sort(key=lambda r: r[0])

    combined: list = []
    warnings: list[str] = []
    report_month: str | None = None
    failed_count = 0
    for idx, rows, month, err in results:
        if err is not None:
            failed_count += 1
            warnings.append(
                f"Chunk {idx + 1} of {n} failed after retries "
                f"({type(err).__name__}: {err}). Some rows from that section "
                f"of the file are missing — re-upload to retry."
            )
        else:
            combined.extend(rows)
            if report_month is None and month:
                report_month = month

    total_ms = (time.perf_counter() - t_total) * 1000
    logger.info(
        "%s gemini-chunked: done chunks_ok=%d/%d combined_rows=%d total_ms=%.0f",
        log_prefix, n - failed_count, n, len(combined), total_ms,
    )

    if failed_count == n:
        # All chunks failed — propagate the first error so the OpenAI fallback
        # in parse_with_llm gets a chance.
        first_err = next((e for _, _, _, e in results if e is not None), None)
        assert first_err is not None
        raise first_err

    return combined, warnings, report_month


# ---------------------------------------------------------------------------
# OpenAI  (fallback) — text only; PDFs are pre-converted via pdfplumber
# ---------------------------------------------------------------------------

def _pdf_to_text(pdf_bytes: bytes, *, log_prefix: str = "") -> str:
    import pdfplumber

    t0 = time.perf_counter()
    texts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            texts.append(t)
    out = "\n\n".join(texts)
    logger.info(
        "%s pdf_to_text: pages=%d chars=%d elapsed_ms=%.0f",
        log_prefix, len(texts), len(out), (time.perf_counter() - t0) * 1000,
    )
    return out


def _call_openai(req: LLMParseRequest) -> tuple[dict, str]:
    from openai import OpenAI  # type: ignore

    log_prefix = req.log_prefix
    t_total = time.perf_counter()

    if req.is_pdf and req.pdf_bytes:
        text_content = _pdf_to_text(req.pdf_bytes, log_prefix=log_prefix)
    else:
        text_content = req.raw_text or ""

    fos_hint = (
        f"\n\nNote: the detected MR/FOS name for this file is '{req.detected_fos_name}'."
        if req.detected_fos_name
        else ""
    )
    user_content = (
        f"Extract all sale rows from the following distributor report.{fos_hint}\n\n{text_content}"
    )

    logger.info(
        "%s openai: starting model=gpt-4o-mini input_chars=%d",
        log_prefix, len(user_content),
    )
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=16384,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception:
        logger.exception(
            "%s openai: call failed after elapsed_ms=%.0f",
            log_prefix, (time.perf_counter() - t_total) * 1000,
        )
        raise

    response_text = response.choices[0].message.content or "{}"
    raw = json.loads(response_text)
    rows_n = len(raw.get("rows", [])) if isinstance(raw, dict) else 0
    logger.info(
        "%s openai: ok response_chars=%d rows=%d elapsed_ms=%.0f",
        log_prefix, len(response_text), rows_n,
        (time.perf_counter() - t_total) * 1000,
    )
    return raw, "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Array-of-arrays → list[dict] conversion
# ---------------------------------------------------------------------------

def _normalize_date(val: object) -> object:
    """
    Coerce common date formats returned by the LLM to YYYY-MM-DD.

    The LLM is instructed to output ISO dates but sometimes returns the
    source format (e.g. "14/2/2026" or "14-02-2026") — especially when
    processing a document chunk without the full-document context.
    We normalise deterministically here so the validator never sees a
    non-ISO date that came from a known parseable format.
    """
    if not isinstance(val, str):
        return val
    s = val.strip()
    if not s:
        return val
    # Already ISO YYYY-MM-DD
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    import re as _re
    # DD/MM/YYYY  or  D/M/YYYY  or  DD-MM-YYYY  etc.
    m = _re.fullmatch(r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    # MM/DD/YYYY (unlikely given Indian source data but handle for safety)
    m2 = _re.fullmatch(r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2})", s)
    if m2:
        p1, p2, y2 = m2.group(1), m2.group(2), m2.group(3)
        year = f"20{y2}" if int(y2) < 50 else f"19{y2}"
        return f"{year}-{p2.zfill(2)}-{p1.zfill(2)}"
    return val


def _normalize_qty(val: object) -> object:
    """
    Ensure sale_qty / free_qty are returned as numbers.

    The LLM occasionally returns fractional quantities (5.5, 0.5) as
    strings, or returns summary-row values. Coerce to float/int so the
    validator receives a numeric type, matching what the single-call path
    has always produced.
    """
    if val is None or isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        s = val.strip()
        try:
            f = float(s)
            return int(f) if f == int(f) else f
        except ValueError:
            pass
    return val


def _is_junk_row(d: dict) -> bool:
    """
    Identify summary / non-transaction rows that the LLM occasionally includes
    when processing a document chunk without full-document context (e.g.
    "Party Total ->", "Grand Total", customer-section header rows).

    A real sales row MUST have a product name and a sale qty. (A missing date is
    NO LONGER junk: monthly-summary reports have no per-row date — those rows get
    the report month filled in downstream, so we must keep them here.)
    """
    product = d.get("product_name_raw")
    if not isinstance(product, str) or not product.strip():
        return True
    qty = d.get("sale_qty")
    if qty is None or qty == "" or qty == "null":
        return True
    return False


def _rows_to_dicts(raw_rows: list) -> list[dict]:
    result: list[dict] = []
    junk_dropped = 0
    junk_samples: list[dict] = []
    logger.info(
        "_rows_to_dicts v2: starting raw_rows=%d (junk-filter active)",
        len(raw_rows),
    )
    for row in raw_rows:
        d: dict | None = None
        if isinstance(row, dict):
            # Named-object output: map the model's keys → internal field names.
            d = {}
            for k, v in row.items():
                internal = _KEY_ALIASES.get(str(k).strip().lower())
                if internal and (internal not in d or d[internal] in (None, "")):
                    d[internal] = v
        elif isinstance(row, (list, tuple)):
            # Legacy positional-array fallback (kept for safety).
            d = {}
            for i, col in enumerate(_COLUMNS):
                d[col] = row[i] if i < len(row) else None
        else:
            continue
        d["sale_date"] = _normalize_date(d.get("sale_date"))
        d["sale_qty"] = _normalize_qty(d.get("sale_qty"))
        d["free_qty"] = _normalize_qty(d.get("free_qty"))
        if _is_junk_row(d):
            junk_dropped += 1
            if len(junk_samples) < 3:
                junk_samples.append({
                    "product": d.get("product_name_raw"),
                    "date": d.get("sale_date"),
                    "qty": d.get("sale_qty"),
                })
            continue
        result.append(d)
    logger.info(
        "_rows_to_dicts v2: dropped %d junk row(s) from %d raw → %d kept "
        "(samples=%s)",
        junk_dropped, len(raw_rows), len(result), junk_samples,
    )
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_with_llm(req: LLMParseRequest) -> LLMParseResponse:
    """
    Convert document to structured rows via LLM.

    For PDFs with MINERU_API_KEY set:
      - MinerU converts PDF → markdown (no Gemini Files API upload)
      - Gemini / OpenAI receive markdown as a plain text prompt

    For PDFs without MINERU_API_KEY:
      - Falls back to Gemini Files API binary upload (original behaviour)

    For tabular files (CSV/XLSX/XLS):
      - Already text from extractor.py; MinerU is not called.
    """
    log_prefix = req.log_prefix
    t_total = time.perf_counter()
    logger.info(
        "%s parse_with_llm: starting is_pdf=%s pdf_bytes=%d text_chars=%d "
        "mineru_enabled=%s gemini_enabled=%s openai_enabled=%s "
        "chunk_count=%d chunk_min_chars=%d",
        log_prefix, req.is_pdf,
        len(req.pdf_bytes or b""), len(req.raw_text or ""),
        bool(settings.MINERU_API_KEY),
        bool(settings.GEMINI_API_KEY),
        bool(settings.OPENAI_API_KEY),
        settings.IMPORT_CHUNK_COUNT,
        settings.IMPORT_CHUNK_MIN_CHARS,
    )

    # --- Step 0: MinerU pre-extraction for PDFs ---
    # Run this BEFORE any LLM call so both Gemini and the OpenAI fallback
    # receive clean markdown text rather than a binary PDF.
    effective_req = req
    came_from_mineru = False
    if req.is_pdf and req.pdf_bytes and settings.MINERU_API_KEY:
        try:
            markdown = _call_mineru_extract(req.pdf_bytes, log_prefix=log_prefix)
            effective_req = LLMParseRequest(
                raw_text=markdown, is_pdf=False, log_prefix=log_prefix,
            )
            came_from_mineru = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s parse_with_llm: MinerU failed (%s: %s) — falling back to Gemini Files API",
                log_prefix, type(exc).__name__, exc,
            )
            # effective_req stays as original req; Gemini Files API path used below

    # Chunking is only safe when:
    #   - we have markdown from MinerU (heading-structured, dedupe-friendly)
    #   - text is large enough that the savings outweigh the overhead
    #   - chunk count is configured > 1
    # We do NOT chunk:
    #   - binary PDF (Gemini Files API)
    #   - tabular CSV/XLSX text (no heading structure; chunking risks losing
    #     header context across slices)
    can_chunk = (
        came_from_mineru
        and settings.IMPORT_CHUNK_COUNT > 1
        and len(effective_req.raw_text or "") >= settings.IMPORT_CHUNK_MIN_CHARS
    )

    raw_rows: list = []
    model_used = ""
    chunk_warnings: list[str] = []
    report_month: str | None = None
    pre_dedupe_count = 0
    actual_chunks_used = 1

    _gemini_error: Exception | None = None
    if settings.GEMINI_API_KEY:
        try:
            if can_chunk:
                chunks = _split_markdown_into_chunks(
                    effective_req.raw_text or "",
                    settings.IMPORT_CHUNK_COUNT,
                )
                if len(chunks) <= 1:
                    # Both heading-split and table-row-split refused — the
                    # markdown structure isn't recognisable enough to chunk
                    # safely. Accuracy is preserved; latency is unchanged.
                    # Log at WARNING so this is easy to monitor in production.
                    md = effective_req.raw_text or ""
                    md_lines = md.splitlines()
                    heading_count = sum(1 for ln in md_lines if ln.lstrip().startswith("#"))
                    table_row_count = sum(1 for ln in md_lines if ln.strip().startswith("|"))
                    logger.warning(
                        "%s parse_with_llm: chunking SKIPPED (both strategies "
                        "failed) — markdown_chars=%d heading_lines=%d "
                        "table_rows=%d requested_chunks=%d. "
                        "Falling back to single call — accuracy preserved.",
                        log_prefix, len(md), heading_count,
                        table_row_count, settings.IMPORT_CHUNK_COUNT,
                    )
                    raw, model_used = _call_gemini(effective_req)
                    raw_rows = raw.get("rows", []) if isinstance(raw, dict) else []
                    if not isinstance(raw_rows, list):
                        raw_rows = []
                    report_month = raw.get("report_month") if isinstance(raw, dict) else None
                else:
                    actual_chunks_used = len(chunks)
                    raw_rows, chunk_warnings, report_month = _call_gemini_chunked(
                        chunks,
                        detected_fos_name=effective_req.detected_fos_name,
                        log_prefix=log_prefix,
                    )
                    model_used = _GEMINI_MODEL
            else:
                raw, model_used = _call_gemini(effective_req)
                raw_rows = raw.get("rows", []) if isinstance(raw, dict) else []
                if not isinstance(raw_rows, list):
                    raw_rows = []
                report_month = raw.get("report_month") if isinstance(raw, dict) else None
        except Exception as exc:  # noqa: BLE001
            _gemini_error = exc
            logger.warning(
                "%s parse_with_llm: Gemini failed (%s: %s) — trying OpenAI fallback",
                log_prefix, type(exc).__name__, exc,
            )

    if not model_used and settings.OPENAI_API_KEY:
        try:
            raw, model_used = _call_openai(effective_req)
            raw_rows = raw.get("rows", []) if isinstance(raw, dict) else []
            if not isinstance(raw_rows, list):
                raw_rows = []
            report_month = raw.get("report_month") if isinstance(raw, dict) else None
        except Exception as exc:
            logger.error(
                "%s parse_with_llm: OpenAI fallback also failed (%s: %s)",
                log_prefix, type(exc).__name__, exc,
            )
            raise RuntimeError("Both LLM providers failed. Check API keys and connectivity.") from exc

    if not model_used:
        if not settings.GEMINI_API_KEY and not settings.OPENAI_API_KEY:
            raise RuntimeError("No LLM API keys configured. Set GEMINI_API_KEY or OPENAI_API_KEY in .env")
        if _gemini_error is not None:
            raise RuntimeError(
                f"Gemini call failed: {type(_gemini_error).__name__}: {_gemini_error}"
            ) from _gemini_error
        raise RuntimeError("LLM call returned no model_used (unknown reason)")

    # Dedupe across ALL paths (single-call tabular/PDF + chunked). The model
    # occasionally emits the same invoice line twice; without this it inflates
    # qty/amount. Conservative: only drops a row when its full identity
    # (date, customer, product, qty, bill_ref) is present and matches exactly.
    pre_dedupe_count = len(raw_rows)
    raw_rows, removed = _dedupe_rows(raw_rows)
    if removed:
        logger.info(
            "%s parse_with_llm: dedupe removed %d duplicate row(s) (kept %d)",
            log_prefix, removed, len(raw_rows),
        )

    rows = _rows_to_dicts(raw_rows)

    logger.info(
        "%s parse_with_llm: done model=%s chunks_used=%d raw_rows=%d "
        "(pre_dedupe=%d) kept_rows=%d warnings=%d total_ms=%.0f",
        log_prefix, model_used, actual_chunks_used,
        len(raw_rows), pre_dedupe_count or len(raw_rows),
        len(rows), len(chunk_warnings),
        (time.perf_counter() - t_total) * 1000,
    )

    return LLMParseResponse(
        rows=rows,
        model_used=model_used,
        raw_response={"rows": raw_rows},
        warnings=chunk_warnings,
        report_month=report_month,
    )
