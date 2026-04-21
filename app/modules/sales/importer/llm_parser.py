"""
LLM-based parser — Approach G: Gemini Files API + no entity lists in prompt.

For PDFs  : uploads file via Gemini Files API, calls generateContent with the
            file_uri, then immediately deletes the uploaded file.
For tabular: sends minimal CSV text directly (no file upload needed).

The LLM extracts raw name strings only.  Entity→UUID resolution is done
entirely on the backend using difflib fuzzy matching (see import_service.py).

Primary model : gemini-2.5-flash-lite
Fallback model: gpt-4o-mini  (text-only; for PDFs it falls back to pdfplumber
                               text extraction before sending to OpenAI)
"""
from __future__ import annotations

import io
import json
import logging
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
# Gemini  (primary) — Files API for PDFs, direct text for tabular
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

    last_exc: Exception | None = None
    for attempt in range(_GEMINI_MAX_ATTEMPTS):
        file_obj = None
        try:
            if req.is_pdf and req.pdf_bytes:
                file_obj = genai.upload_file(
                    io.BytesIO(req.pdf_bytes),
                    mime_type="application/pdf",
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

            response = model.generate_content(
                user_parts,
                request_options={"timeout": _GEMINI_TIMEOUT_S},
            )
            return json.loads(response.text), _GEMINI_MODEL

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt + 1 < _GEMINI_MAX_ATTEMPTS and _is_transient_gemini_error(exc):
                delay = _GEMINI_BACKOFF_SECONDS[attempt]
                logger.warning(
                    "Gemini attempt %d/%d failed (%s), retrying in %ds",
                    attempt + 1, _GEMINI_MAX_ATTEMPTS, type(exc).__name__, delay,
                )
                time.sleep(delay)
            else:
                break

        finally:
            # Always delete the uploaded file, even on error, to avoid storage accumulation
            if file_obj is not None:
                try:
                    file_obj.delete()
                except Exception:
                    pass

    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# OpenAI  (fallback) — text only; PDFs are pre-converted via pdfplumber
# ---------------------------------------------------------------------------

def _pdf_to_text(pdf_bytes: bytes) -> str:
    import pdfplumber

    texts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            texts.append(t)
    return "\n\n".join(texts)


def _call_openai(req: LLMParseRequest) -> tuple[dict, str]:
    from openai import OpenAI  # type: ignore

    if req.is_pdf and req.pdf_bytes:
        text_content = _pdf_to_text(req.pdf_bytes)
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

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
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
    raw = json.loads(response.choices[0].message.content or "{}")
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
    """Upload file (PDFs) or send text (tabular) to LLM; return structured rows."""
    raw: dict = {}
    model_used = ""

    _gemini_error: Exception | None = None
    if settings.GEMINI_API_KEY:
        try:
            raw, model_used = _call_gemini(req)
        except Exception as exc:  # noqa: BLE001
            _gemini_error = exc
            logger.warning("Gemini failed (%s: %s), trying OpenAI fallback", type(exc).__name__, exc)

    if not model_used and settings.OPENAI_API_KEY:
        try:
            raw, model_used = _call_openai(req)
        except Exception as exc:
            logger.error("OpenAI fallback also failed: %s", exc)
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

    return LLMParseResponse(
        rows=_rows_to_dicts(raw_rows),
        model_used=model_used,
        raw_response=raw,
    )
