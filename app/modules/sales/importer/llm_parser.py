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

def _rows_to_dicts(raw_rows: list) -> list[dict]:
    result: list[dict] = []
    for row in raw_rows:
        if isinstance(row, dict):
            # LLM returned named objects (shouldn't happen but handle gracefully)
            result.append(row)
        elif isinstance(row, (list, tuple)):
            d: dict = {}
            for i, col in enumerate(_COLUMNS):
                d[col] = row[i] if i < len(row) else None
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
        "mineru_enabled=%s gemini_enabled=%s openai_enabled=%s",
        log_prefix, req.is_pdf,
        len(req.pdf_bytes or b""), len(req.raw_text or ""),
        bool(settings.MINERU_API_KEY),
        bool(settings.GEMINI_API_KEY),
        bool(settings.OPENAI_API_KEY),
    )

    # --- Step 0: MinerU pre-extraction for PDFs ---
    # Run this BEFORE any LLM call so both Gemini and the OpenAI fallback
    # receive clean markdown text rather than a binary PDF.
    effective_req = req
    if req.is_pdf and req.pdf_bytes and settings.MINERU_API_KEY:
        try:
            markdown = _call_mineru_extract(req.pdf_bytes, log_prefix=log_prefix)
            effective_req = LLMParseRequest(
                raw_text=markdown, is_pdf=False, log_prefix=log_prefix,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s parse_with_llm: MinerU failed (%s: %s) — falling back to Gemini Files API",
                log_prefix, type(exc).__name__, exc,
            )
            # effective_req stays as original req; Gemini Files API path used below

    raw: dict = {}
    model_used = ""

    _gemini_error: Exception | None = None
    if settings.GEMINI_API_KEY:
        try:
            raw, model_used = _call_gemini(effective_req)
        except Exception as exc:  # noqa: BLE001
            _gemini_error = exc
            logger.warning(
                "%s parse_with_llm: Gemini failed (%s: %s) — trying OpenAI fallback",
                log_prefix, type(exc).__name__, exc,
            )

    if not model_used and settings.OPENAI_API_KEY:
        try:
            raw, model_used = _call_openai(effective_req)
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

    raw_rows = raw.get("rows", [])
    if not isinstance(raw_rows, list):
        raw_rows = []
    rows = _rows_to_dicts(raw_rows)

    logger.info(
        "%s parse_with_llm: done model=%s raw_rows=%d kept_rows=%d total_ms=%.0f",
        log_prefix, model_used, len(raw_rows), len(rows),
        (time.perf_counter() - t_total) * 1000,
    )

    return LLMParseResponse(
        rows=rows,
        model_used=model_used,
        raw_response=raw,
    )
