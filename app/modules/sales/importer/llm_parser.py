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
    "sale_date",          # 1  YYYY-MM-DD
    "sale_qty",           # 2  integer
    "free_qty",           # 3  integer (0 if absent)
    "mrp",                # 4  decimal
    "ptr",                # 5  decimal Rate / selling price
    "reported_amount",    # 6  decimal Amount / Value total
    "bill_ref",           # 7  bill/invoice reference string
    "batch",              # 8  batch number
    "pack",               # 9  pack size/form  e.g. "1X10TA"
    "customer_name_raw",  # 10 exact party/store name from file
    "mr_name_raw",        # 11 FOS/MR name (null for single-MR files)
    "doctor_name_raw",    # 12 doctor name (null if not present)
]

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


# ---------------------------------------------------------------------------
# Shared system prompt  (no entity lists — kept intentionally compact)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a data extraction assistant for a pharmaceutical CRM.
Extract every secondary sale transaction row from the provided distributor sales report.

Return JSON: {"rows": [[col0, col1, ..., col12], ...]}

Column order — exactly 13 values per row:
  0  product_name     exact product name string from the file
  1  sale_date        date as YYYY-MM-DD; null if absent
  2  sale_qty         integer quantity sold; null if absent
  3  free_qty         integer free quantity; 0 if absent
  4  mrp              decimal MRP; null if absent
  5  ptr              decimal Rate / selling price; null if absent
  6  reported_amount  decimal Amount / Value total as reported; null if absent
  7  bill_ref         bill/invoice reference string; null if absent
  8  batch            batch number string; null if absent
  9  pack             pack size/form e.g. "1X10TA"; null if absent
  10 customer_name    exact party / store name from the file; null if absent
  11 mr_name          FOS / MR name from the file; null if not present per-row
  12 doctor_name      doctor name from the file; null if not present

Rules:
- Skip subtotal rows ("Party Total", "Grand Total", "Customer Total") and page headers.
- If a customer section header introduces a block of rows for one customer, repeat
  that customer name in column 10 of every row in that block.
- Parse all date formats to YYYY-MM-DD.
- Numbers may use comma thousand-separators — parse them as plain decimals.
- Do NOT invent data. Only extract what is explicitly present in the file.\
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
# Two PDF output structures from MinerU, both handled:
#
#  Structure A — "section-per-customer" (structured PDF):
#    ## Customer: ABC Pharmacy
#    | Product | Qty | ...
#    ## Customer: XYZ Drug
#    | Product | Qty | ...
#    → Split at `#` heading boundaries. Each chunk gets self-contained sections.
#
#  Structure B — "multi-page table" (distributor report, most common):
#    MinerU emits ONE separate markdown table per PDF page.
#    Each page table starts with a column-header row + |---| separator, then
#    customer-section rows (colspan → | CUSTOMER NAME | | | | ... |) and
#    data rows.  Customers that span pages are safe to split: MinerU repeats
#    the customer-name row on the continuation page's table.
#    → Detect all table-start (header+separator) pairs; split at page boundaries.

def _split_markdown_into_chunks(text: str, n: int) -> list[str]:
    """
    Split MinerU markdown into `n` safe parallel chunks.

    Strategy (tried in order — stops at first that works):

    1. Heading split: split at `#` boundaries. For section-per-customer PDFs
       where MinerU emits one `##` heading per customer. Requires ≥ n-1
       heading boundaries after line 0.

    2. Page-table-boundary split: for multi-page distributor reports where
       MinerU emits one markdown table per PDF page. Detects each table-start
       (column-header row + |---| separator) and splits at page boundaries.
       Safe because cross-page customer sections have their header row repeated
       by MinerU on the continuation page. Requires ≥ n page tables.

    3. Single call: if neither works, return [text]. The caller detects this
       and uses single-call mode (accuracy preserved, no speedup).
    """
    if n <= 1 or not text:
        return [text] if text else []

    lines = text.splitlines(keepends=True)
    if len(lines) < n * 2:
        return [text]

    # --- Strategy 1: heading-based split ---
    heading_idxs = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("#")]
    boundary_candidates = [i for i in heading_idxs if i > 0]
    if len(boundary_candidates) >= n - 1:
        L = len(boundary_candidates)
        if L == n - 1:
            boundaries = list(boundary_candidates)
        else:
            boundaries = [boundary_candidates[(i * L) // n] for i in range(1, n)]
        boundaries = sorted(set(boundaries))
        chunks: list[str] = []
        prev = 0
        for b in boundaries:
            chunks.append("".join(lines[prev:b]))
            prev = b
        chunks.append("".join(lines[prev:]))
        chunks = [c for c in chunks if c.strip()]
        if len(chunks) >= 2:
            return chunks

    # --- Strategy 2: page-table-boundary split ---
    #
    # MinerU emits ONE markdown table per PDF page. Each page table starts with
    # a column-header row immediately followed by a |---| separator row, e.g.:
    #
    #   | Product | Pack | BillRef | Date | MRP Batch | Qty | Free | Rate | Amount |
    #   |---|---|---|---|---|---|---|---|---|
    #   | KRISHANA MEDICINES, RAJKOT... | | | | | | | | |   ← customer-section row
    #   | JUGSI DM FORTE TAB | 10 TAB | GCD/757 | ...      ← data row
    #   ...
    #
    # Customer sections that span PDF pages are safe to split between: MinerU
    # repeats the customer-name row at the top of the continuation table on the
    # next page, so every chunk is self-contained and the LLM never loses context.

    def _is_sep(ln: str) -> bool:
        s = ln.strip()
        return (
            s.startswith("|")
            and bool(s)
            and all(c in "|-: \t" for c in s)
            and "-" in s
        )

    # Walk lines once to find every table-start position:
    # a non-separator | row immediately followed (skipping blanks) by a separator.
    table_start_idxs: list[int] = []
    k = 0
    while k < len(lines):
        ln = lines[k]
        stripped = ln.strip()
        if stripped.startswith("|") and not _is_sep(ln):
            # Peek ahead for separator
            j = k + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _is_sep(lines[j]):
                table_start_idxs.append(k)
                k = j + 1
                continue
        k += 1

    logger.info(
        "split: strategy2 found %d page-table boundaries (need >= %d)",
        len(table_start_idxs), n,
    )
    # Need at least n distinct page-tables to split into n chunks.
    if len(table_start_idxs) >= n:
        # Candidate boundaries: every table-start after the very first one.
        boundary_candidates = table_start_idxs[1:]
        L = len(boundary_candidates)
        if L >= n - 1:
            if L == n - 1:
                boundaries = list(boundary_candidates)
            else:
                boundaries = [boundary_candidates[(m * L) // n] for m in range(1, n)]
            boundaries = sorted(set(boundaries))
            chunks: list[str] = []
            prev = 0
            for b in boundaries:
                chunks.append("".join(lines[prev:b]))
                prev = b
            chunks.append("".join(lines[prev:]))
            chunks = [c for c in chunks if c.strip()]
            if len(chunks) >= 2:
                return chunks

    # --- Strategy 3: line-count split at safe line boundaries ---
    #
    # Both structure-aware strategies failed (no headings, no page-table pattern).
    # Fall back to splitting the document into n roughly equal parts by character
    # count, but always cut at a newline boundary — never mid-row. This is safe
    # even for markdown tables because each row is self-contained on its own line.
    #
    # Accuracy risk is low: MinerU repeats context rows (customer name) at page
    # breaks, so the LLM in each chunk has enough context even without seeing the
    # full document. A small overlap is NOT needed here because MinerU's output is
    # row-oriented (one sale per line).
    total_chars = sum(len(ln) for ln in lines)
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
        logger.info(
            "split: strategy3 line-count produced %d chunks from %d chars",
            len(chunks_fb), total_chars,
        )
        return chunks_fb

    return [text]


# ---------------------------------------------------------------------------
# Row deduplication — composite key on the natural identity of a sale row
# ---------------------------------------------------------------------------
#
# Used after combining rows from parallel chunks. A row is uniquely identified
# by (sale_date, customer_name, product_name, sale_qty, bill_ref). Repeats are
# extremely rare for legitimate data — same product to same customer on same
# day with same qty and same invoice ref — so collisions almost always mean
# the LLM emitted the same physical row twice across chunk boundaries.

def _dedupe_rows(raw_rows: list) -> tuple[list, int]:
    """
    Returns (unique_rows, removed_count). Order-stable; first occurrence wins.

    Conservatism: we ONLY drop a duplicate when every component of the
    composite key is non-empty. A row missing any key component (e.g. the
    LLM didn't catch the bill_ref) is always kept — better to surface a
    duplicate the user can review than to silently merge two legitimate
    rows that happen to share product/customer/date/qty.

    Composite key = (sale_date, customer, product, sale_qty, bill_ref).
    """
    seen: set[tuple] = set()
    unique: list = []

    for row in raw_rows:
        if isinstance(row, (list, tuple)):
            # _COLUMNS index map: 0 product, 1 sale_date, 2 sale_qty,
            # 7 bill_ref, 10 customer_name
            key = (
                str(row[1] or "").strip() if len(row) > 1 else "",
                str(row[10] or "").strip() if len(row) > 10 else "",
                str(row[0] or "").strip() if len(row) > 0 else "",
                str(row[2] or "").strip() if len(row) > 2 else "",
                str(row[7] or "").strip() if len(row) > 7 else "",
            )
        elif isinstance(row, dict):
            key = (
                str(row.get("sale_date") or "").strip(),
                str(row.get("customer_name_raw") or row.get("customer_name") or "").strip(),
                str(row.get("product_name_raw") or row.get("product_name") or "").strip(),
                str(row.get("sale_qty") or "").strip(),
                str(row.get("bill_ref") or "").strip(),
            )
        else:
            unique.append(row)
            continue

        # Conservative: only consider deduping when EVERY key component is
        # populated. Missing fields → can't tell duplicate from coincidence,
        # so always keep the row. The user reviews preview and can manually
        # remove true duplicates.
        if not all(key):
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

    def _do_one(idx: int, chunk_text: str) -> tuple[int, list, Exception | None]:
        chunk_prefix = f"{log_prefix}[c{idx + 1}/{n}]"
        user_part = (
            f"Extract all sale rows from the following distributor report "
            f"(this is part {idx + 1} of {n}, sent in parallel — extract only "
            f"what is in this part).{fos_hint}\n\n{chunk_text}"
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
                logger.info(
                    "%s gemini ok response_chars=%d rows=%d "
                    "attempt_ms=%.0f total_ms=%.0f",
                    chunk_prefix, len(response_text), len(rows),
                    attempt_ms, (time.perf_counter() - t_chunk) * 1000,
                )
                return idx, rows, None

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

        return idx, [], last_exc

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
    failed_count = 0
    for idx, rows, err in results:
        if err is not None:
            failed_count += 1
            warnings.append(
                f"Chunk {idx + 1} of {n} failed after retries "
                f"({type(err).__name__}: {err}). Some rows from that section "
                f"of the file are missing — re-upload to retry."
            )
        else:
            combined.extend(rows)

    total_ms = (time.perf_counter() - t_total) * 1000
    logger.info(
        "%s gemini-chunked: done chunks_ok=%d/%d combined_rows=%d total_ms=%.0f",
        log_prefix, n - failed_count, n, len(combined), total_ms,
    )

    if failed_count == n:
        # All chunks failed — propagate the first error so the OpenAI fallback
        # in parse_with_llm gets a chance.
        first_err = next((e for _, _, e in results if e is not None), None)
        assert first_err is not None
        raise first_err

    return combined, warnings


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


def _rows_to_dicts(raw_rows: list) -> list[dict]:
    result: list[dict] = []
    for row in raw_rows:
        if isinstance(row, dict):
            row = dict(row)
            row["sale_date"] = _normalize_date(row.get("sale_date"))
            row["sale_qty"] = _normalize_qty(row.get("sale_qty"))
            row["free_qty"] = _normalize_qty(row.get("free_qty"))
            result.append(row)
        elif isinstance(row, (list, tuple)):
            d: dict = {}
            for i, col in enumerate(_COLUMNS):
                d[col] = row[i] if i < len(row) else None
            d["sale_date"] = _normalize_date(d.get("sale_date"))
            d["sale_qty"] = _normalize_qty(d.get("sale_qty"))
            d["free_qty"] = _normalize_qty(d.get("free_qty"))
            result.append(d)
        # skip anything else (nulls, strings, etc.)
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
                else:
                    actual_chunks_used = len(chunks)
                    raw_rows, chunk_warnings = _call_gemini_chunked(
                        chunks,
                        detected_fos_name=effective_req.detected_fos_name,
                        log_prefix=log_prefix,
                    )
                    pre_dedupe_count = len(raw_rows)
                    raw_rows, removed = _dedupe_rows(raw_rows)
                    if removed:
                        logger.info(
                            "%s parse_with_llm: dedupe removed %d duplicate row(s) "
                            "across chunks (kept %d)",
                            log_prefix, removed, len(raw_rows),
                        )
                    model_used = _GEMINI_MODEL
            else:
                raw, model_used = _call_gemini(effective_req)
                raw_rows = raw.get("rows", []) if isinstance(raw, dict) else []
                if not isinstance(raw_rows, list):
                    raw_rows = []
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
    )
